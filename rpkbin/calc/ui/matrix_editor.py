"""Virtual matrix editor with bounded, kernel-backed page caching."""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QSizePolicy, QTableView, QToolButton, QVBoxLayout,
)

from ..protocol import PAGE_COLS, PAGE_ROWS
from ..core.format_engine import FormatEngine
from .icons import style_icon_button
from .theme import get_palette, scaled_px
from .numeric_display import format_numeric_text
from .i18n import UiText, notify, t


class MatrixTableModel(QAbstractTableModel):
    MAX_CACHED_PAGES = 8

    def __init__(self, name, controller, parent=None):
        super().__init__(parent)
        self.name = name
        self.controller = controller
        self.snapshot = None
        self.revision = -1
        self.mode = "real"
        self.editing_enabled = False
        self._cache = OrderedDict()
        self._pending = set()
        self._display_signature = (controller.config.numeric_display, controller.config.numeric_precision)
        self._language = (controller.config.language, controller.config.keep_english_terms)
        controller.matrix_page_received.connect(self._receive_page)
        controller.interaction_enabled.connect(self._set_editing_enabled)
        controller.config_changed.connect(self._refresh_display)

    def _refresh_display(self, cfg):
        if self._language != (cfg.language, cfg.keep_english_terms):
            self._language = (cfg.language, cfg.keep_english_terms)
            if self.columnCount():
                self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, self.columnCount()-1)
            if self.rowCount():
                self.headerDataChanged.emit(Qt.Orientation.Vertical, 0, self.rowCount()-1)
        signature = (cfg.numeric_display, cfg.numeric_precision)
        if signature == self._display_signature:
            return
        self._display_signature = signature
        if self.rowCount() and self.columnCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount()-1, self.columnCount()-1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.snapshot is None else self.snapshot.shape[0]

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.snapshot is None else self.snapshot.shape[1]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return t("Col {index}" if orientation == Qt.Orientation.Horizontal else "Row {index}",
                     self.controller.config.language, keep_english_terms=self.controller.config.keep_english_terms, index=section)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.snapshot is None:
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        key = (index.row() // PAGE_ROWS * PAGE_ROWS, index.column() // PAGE_COLS * PAGE_COLS)
        page = self._cache.get(key)
        if page is None:
            if key not in self._pending and len(self._pending) < 32:
                self._pending.add(key)
                self.controller.request_matrix_page(self.name, *key)
            return "…"
        self._cache.move_to_end(key)
        grid = getattr(page, "cells_" + self.mode)
        text = grid[index.row() - page.row_offset][index.column() - page.col_offset]
        return (format_numeric_text(text, self.controller.config)
                if role == Qt.ItemDataRole.DisplayRole and self.mode == "real" else text)

    def flags(self, index):
        flags = super().flags(index)
        if (index.isValid() and self.snapshot is not None and self.snapshot.editable
                and self.editing_enabled and self.data(index) != "…"):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not (self.flags(index) & Qt.ItemFlag.ItemIsEditable):
            return False
        return self.controller.edit_target_field(self.name, (index.row(), index.column()),
                                                self.mode, str(value))

    def _set_editing_enabled(self, enabled):
        self.editing_enabled = enabled

    def set_snapshot(self, snapshot, revision):
        shape_changed = self.snapshot is None or snapshot.shape != self.snapshot.shape
        changed = shape_changed or revision != self.revision
        if shape_changed:
            self.beginResetModel()
        self.snapshot = snapshot
        self.revision = revision
        if changed:
            self._cache.clear()
            self._pending.clear()
        if shape_changed:
            self.endResetModel()
        elif changed and self.rowCount() and self.columnCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount()-1, self.columnCount()-1))

    def set_mode(self, mode):
        self.mode = mode
        if self.rowCount() and self.columnCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount()-1, self.columnCount()-1))

    def _receive_page(self, page, revision):
        if page.name != self.name or revision != self.revision:
            return
        key = (page.row_offset, page.col_offset)
        self._pending.discard(key)
        self._cache[key] = page
        self._cache.move_to_end(key)
        while len(self._cache) > self.MAX_CACHED_PAGES:
            self._cache.popitem(last=False)
        if page.cells_real and page.cells_real[0]:
            self.dataChanged.emit(self.index(page.row_offset, page.col_offset),
                self.index(page.row_offset+len(page.cells_real)-1,
                           page.col_offset+len(page.cells_real[0])-1))


class MatrixEditorPage(QFrame):
    def __init__(self, var_name, controller, parent=None):
        super().__init__(parent)
        self.var_name = var_name
        self.controller = controller
        self._text = UiText(controller.config.language, keep_english_terms=controller.config.keep_english_terms)
        self._error_source = None
        self.palette = get_palette(controller.config.theme)
        self._last_snapshot = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        toolbar = QFrame(self)
        toolbar.setObjectName("PanelHeader")
        # A wide child layout must not prevent the parent from shrinking far
        # enough to trigger its narrow layout on the next resize event.
        toolbar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        toolbar_layout = QGridLayout(toolbar)
        self._toolbar_layout = toolbar_layout
        self._toolbar_wide = None
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.shape_lbl = QLabel(var_name)
        self.shape_lbl.setWordWrap(True)
        self.shape_lbl.setMinimumWidth(0)
        self.shape_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        toolbar_layout.addWidget(self.shape_lbl, 0, 0, 1, 2)
        self._format_host = QFrame(toolbar)
        self._format_layout = QVBoxLayout(self._format_host)
        self._format_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.addWidget(self._format_host, 1, 0, 1, 2)
        self.fmt_lbl = QLabel("Array format:")
        self.fmt_lbl.setWordWrap(True)
        self.fmt_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._format_layout.addWidget(self.fmt_lbl)
        row = QHBoxLayout()
        self._format_row = row
        self._format_layout.addLayout(row)
        row.setContentsMargins(6, 4, 6, 4)
        self.fmt_edit = QLineEdit(toolbar)
        self.fmt_edit.setMinimumWidth(0)
        self.fmt_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.fmt_edit.setToolTip("FXP format applies to the entire array. Qm.n includes sign in m; total bits = m + n. "
                                 "Use Q, MATLAB or a uniquely reversible current custom template. "
                                 "Native arrays: use Inspector conversion controls.")
        self.fmt_edit.returnPressed.connect(self._on_format_edited)
        self.fmt_edit.setPlaceholderText("Enter to apply")
        row.addWidget(self.fmt_edit, 1)
        self.copy_btn = QToolButton(toolbar)
        self.copy_btn.clicked.connect(lambda: controller.copy_matrix(self.var_name, self.model.mode))
        self.preview_btn = QToolButton(toolbar)
        self.preview_btn.clicked.connect(lambda: controller.request_preview(self.var_name, self.fmt_edit.text()))
        row.addWidget(self.preview_btn)
        row.addWidget(self.copy_btn)
        self._modes_host = QFrame(toolbar)
        modes_row = QGridLayout(self._modes_host)
        self._mode_layout = modes_row
        self._mode_columns = None
        modes_row.setContentsMargins(6, 0, 6, 4)
        toolbar_layout.addWidget(self._modes_host, 2, 0, 1, 2)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons = {}
        for mode, label in (("real", "Real"), ("hex", "Hex"), ("s2", "2's"), ("uint", "UInt")):
            button = QToolButton(toolbar)
            button.setText(label)
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            button.setChecked(mode == "real")
            button.clicked.connect(lambda checked, mode=mode: self.model.set_mode(mode))
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            modes_row.addWidget(button, 0, len(self.mode_buttons) - 1)
        self._actions_host = QFrame(toolbar)
        actions_row = QHBoxLayout(self._actions_host)
        actions_row.setContentsMargins(6, 0, 6, 4)
        toolbar_layout.addWidget(self._actions_host, 3, 0, 1, 2)
        self.selection_copy_btn = QToolButton(toolbar)
        self.selection_copy_btn.clicked.connect(self._copy_selection)
        actions_row.addWidget(self.selection_copy_btn)
        self.paste_btn = QToolButton(toolbar)
        self.paste_btn.clicked.connect(self._paste)
        actions_row.addWidget(self.paste_btn)
        self.stats_btn = QToolButton(toolbar)
        self.stats_btn.clicked.connect(self._request_statistics)
        actions_row.addWidget(self.stats_btn)
        actions_row.addStretch()
        self.hint_lbl = QLabel("Select to inspect; double-click to edit.")
        self.hint_lbl.setWordWrap(True)
        self.hint_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        toolbar_layout.addWidget(self.hint_lbl, 4, 0, 1, 2)
        self.slice_edit = QLineEdit(toolbar)
        self.slice_edit.setMinimumWidth(0)
        self.slice_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.slice_edit.setPlaceholderText("Trailing dimension indices: 0, 0")
        self.slice_edit.setToolTip("Select trailing indices for A[:, :, i, j, ...]; changing slice clears GUI Undo")
        self.slice_edit.setAccessibleName("Trailing dimension indices")
        self.slice_edit.editingFinished.connect(self._change_slice)
        toolbar_layout.addWidget(self.slice_edit, 5, 0, 1, 2)
        self.jump_edit = QLineEdit(toolbar)
        self.jump_edit.setMinimumWidth(0)
        self.jump_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.jump_edit.setPlaceholderText("Jump to row, column (zero-based)")
        self.jump_edit.setToolTip("Jump to a zero-based row, column in the current slice")
        self.jump_edit.setAccessibleName("Jump to row and column")
        self.jump_edit.returnPressed.connect(self._jump_to_cell)
        toolbar_layout.addWidget(self.jump_edit, 6, 0, 1, 2)
        layout.addWidget(toolbar)
        self.error_lbl = QLabel(self)
        self.error_lbl.setWordWrap(True)
        self.error_lbl.hide()
        layout.addWidget(self.error_lbl)
        self.retry_edit = QLineEdit(self)
        self.retry_edit.setPlaceholderText("Correct the rejected cell input and press Enter")
        self.retry_edit.hide()
        self.retry_edit.returnPressed.connect(self._retry_cell)
        self._failed_edit = None
        layout.addWidget(self.retry_edit)
        self.table = QTableView(self)
        self.model = MatrixTableModel(var_name, controller, self)
        self.table.setModel(self.model)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
            QAbstractItemView.EditTrigger.EditKeyPressed | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(120)
        self.table.selectionModel().currentChanged.connect(self._on_selection)
        controller.interaction_enabled.connect(self._set_interaction_enabled)
        controller.interaction_enabled.connect(self.copy_btn.setEnabled)
        controller.edit_finished.connect(self._edit_result)
        controller.operation_finished.connect(self._operation_result)
        controller.console_executed.connect(lambda _: self._reset_errors())
        controller.statistics_ready.connect(self._show_statistics)
        controller.config_changed.connect(self.retranslate_ui)
        for sequence, action in ((QKeySequence.StandardKey.Copy, self._copy_selection),
                                 (QKeySequence.StandardKey.Paste, self._paste)):
            shortcut = QShortcut(QKeySequence(sequence), self.table)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(action)
        layout.addWidget(self.table, 1)
        self.apply_appearance(controller.config)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange_toolbar()

    def _arrange_toolbar(self):
        if not hasattr(self, "jump_edit"):
            return
        buttons = list(self.mode_buttons.values())
        mode_width = sum(button.sizeHint().width() for button in buttons)
        mode_width += 3 * self._mode_layout.horizontalSpacing() + 24
        metrics = self.fmt_lbl.fontMetrics()
        margins = self.fmt_lbl.contentsMargins()
        label_width = max(metrics.horizontalAdvance(self.fmt_lbl.text()),
                          metrics.boundingRect(self.fmt_lbl.text()).width())
        label_width += margins.left() + margins.right() + 2 * self.fmt_lbl.margin()
        label_width += 2  # Cover glyph bearing/rounding without feeding the old minimum back into sizing.
        format_width = (label_width
                        + self.fmt_edit.minimumSizeHint().width()
                        + self.copy_btn.width() + self.preview_btn.width() + 48)
        action_width = self._actions_host.minimumSizeHint().width()
        # Two balanced columns avoid a long variable name taking all the space.
        # Use final font/control metrics rather than a fixed font or zoom cutoff.
        required_width = 2 * max(mode_width, format_width, action_width) + 24
        wide = self.width() >= required_width
        # Ignored policy would allocate zero layout width even when the widget
        # enforces a minimum, causing the next input to overlap the label.
        self.fmt_lbl.setMinimumWidth(label_width if wide else 0)
        if wide != self._toolbar_wide:
            self._toolbar_wide = wide
            if wide:
                self._format_layout.removeWidget(self.fmt_lbl)
                self._format_row.insertWidget(0, self.fmt_lbl)
                placements = (
                    (self.shape_lbl, 0, 0, 1), (self._format_host, 0, 1, 1),
                    (self._modes_host, 1, 0, 1), (self._actions_host, 1, 1, 1),
                    (self.hint_lbl, 2, 0, 2),
                    (self.slice_edit, 3, 0, 1), (self.jump_edit, 3, 1, 1),
                )
            else:
                self._format_row.removeWidget(self.fmt_lbl)
                self._format_layout.insertWidget(0, self.fmt_lbl)
                placements = (
                    (self.shape_lbl, 0, 0, 2), (self._format_host, 1, 0, 2),
                    (self._modes_host, 2, 0, 2), (self._actions_host, 3, 0, 2),
                    (self.hint_lbl, 4, 0, 2),
                    (self.slice_edit, 5, 0, 2), (self.jump_edit, 6, 0, 2),
                )
            for widget, row, column, span in placements:
                self._toolbar_layout.addWidget(widget, row, column, 1, span)
            for column in (0, 1):
                self._toolbar_layout.setColumnStretch(column, 1)
            self._toolbar_layout.activate()
        columns = 4 if wide or mode_width <= self.width() else 2
        if columns == self._mode_columns:
            return
        self._mode_columns = columns
        for column in range(4):
            self._mode_layout.setColumnStretch(column, int(column < columns))
        for index, button in enumerate(buttons):
            self._mode_layout.addWidget(button, index // columns, index % columns)
        self._mode_layout.activate()

    def selection_rectangle(self):
        ranges = self.table.selectionModel().selection()
        if ranges.count() != 1:
            raise ValueError("Select one rectangular block")
        selected = ranges.first()
        return (selected.top(), selected.left(), selected.height(), selected.width())

    def _copy_selection(self):
        try:
            self.controller.copy_matrix(self.var_name, self.model.mode, self.selection_rectangle())
        except ValueError:
            self._error("Select one rectangular block")

    def _request_statistics(self):
        try:
            self.controller.request_statistics(self.var_name, self.selection_rectangle())
        except ValueError:
            self._error("Select one rectangular block")

    def _operation_result(self, request, error):
        if request.get("action") in ("paste", "set_slice", "reformat") and request.get("args", [None])[0] == self.var_name:
            self._error("Operation failed: {error}" if error else "", error=error)

    def _paste(self):
        current = self.table.currentIndex()
        if current.isValid():
            self.controller.paste_matrix(self.var_name, current.row(), current.column(),
                                         QGuiApplication.clipboard().text(), self.model.mode)

    def _error(self, source, **values):
        self._error_source = (source, values)
        self.error_lbl.setText(self._text(source, **values))
        self.error_lbl.setVisible(bool(source))

    def _reset_errors(self):
        self._failed_edit = None
        self.retry_edit.hide()
        self._error("")

    def _change_slice(self):
        try:
            indices = [int(item.strip()) for item in self.slice_edit.text().split(",") if item.strip()]
            if self._last_snapshot and tuple(indices) != self._last_snapshot.slice_indices:
                self.controller.set_array_slice(self.var_name, indices)
        except ValueError:
            self._error("Enter comma-separated zero-based trailing indices")

    def _jump_to_cell(self):
        try:
            r, c = (int(item.strip()) for item in self.jump_edit.text().split(","))
            if not (0 <= r < self.model.rowCount() and 0 <= c < self.model.columnCount()):
                raise ValueError()
            index = self.model.index(r, c)
            self.table.setCurrentIndex(index)
            self.table.scrollTo(index)
            self._error("")
        except ValueError:
            self._error("Enter a valid zero-based row, column")

    def _edit_result(self, args, error):
        if args[0] != self.var_name or args[1] is None:
            return
        self._failed_edit = args if error else None
        self._error("Cell {coord} / {mode}: {error}" if error else "", coord=args[1], mode=args[2], error=error)
        self.retry_edit.setVisible(bool(error))
        if error:
            self.retry_edit.setText(args[3])

    def _retry_cell(self):
        if self._failed_edit:
            name, coord, mode, _ = self._failed_edit
            self.controller.edit_target_field(name, coord, mode, self.retry_edit.text())

    def _show_statistics(self, name, result):
        if name == self.var_name:
            notify(self, self._text("Selection statistics"), "\n".join(
                self._text("{label}: {value}", label=self._text(key), value=self._text(value) if key == "note" else value)
                for key, value in result.items()), self.controller.config.language)

    def _set_interaction_enabled(self, enabled):
        self.fmt_edit.setEnabled(bool(enabled and self._last_snapshot and self._last_snapshot.is_fxp))
        for button in (self.stats_btn, self.selection_copy_btn):
            button.setEnabled(enabled)
        self.paste_btn.setEnabled(bool(enabled and self._last_snapshot and self._last_snapshot.editable))
        self.preview_btn.setEnabled(bool(enabled and self._last_snapshot and self._last_snapshot.is_fxp))

    def retranslate_ui(self, cfg):
        """Change application copy without touching model, editor or selection."""
        self._text.refresh(cfg.language, keep_english_terms=cfg.keep_english_terms)
        snapshot = self._last_snapshot
        self.fmt_lbl.setText(self._text("Array format:" if not snapshot or snapshot.is_fxp else "Dtype:"))
        self.fmt_edit.setPlaceholderText(self._text("Enter to apply"))
        self.fmt_edit.setAccessibleName(self._text("Array format"))
        self.fmt_edit.setToolTip(self._text("FXP format applies to the entire array. Qm.n includes sign in m; total bits = m + n. "
            "Use Q, MATLAB or a uniquely reversible current custom template. "
            "Native arrays: use Inspector conversion controls."))
        for widget, placeholder, tooltip, accessible in (
            (self.slice_edit, "Trailing dimension indices: 0, 0", "Select trailing indices for A[:, :, i, j, ...]; changing slice clears GUI Undo", "Trailing dimension indices"),
            (self.jump_edit, "Jump to row, column (zero-based)", "Jump to a zero-based row, column in the current slice", "Jump to row and column"),
        ):
            widget.setPlaceholderText(self._text(placeholder))
            widget.setToolTip(self._text(tooltip))
            widget.setAccessibleName(self._text(accessible))
        self.retry_edit.setPlaceholderText(self._text("Correct the rejected cell input and press Enter"))
        self.retry_edit.setAccessibleName(self._text("Retry cell edit"))
        for button, source in (
            (self.copy_btn, "Copy entire matrix as TSV (up to 100,000 cells)"),
            (self.selection_copy_btn, "Copy selected cells as TSV (Ctrl+C)"),
            (self.paste_btn, "Paste TSV at the selected cell (Ctrl+V)"),
            (self.preview_btn, "Preview format conversion for the whole array"),
            (self.stats_btn, "Show statistics for the selected cells"),
        ):
            button.setToolTip(self._text(source))
            button.setText(button.toolTip())
            button.setAccessibleName(button.toolTip())
        for mode, source in (("real", "Real"), ("hex", "Hex"), ("s2", "2's"), ("uint", "UInt")):
            self.mode_buttons[mode].setText(self._text(source))
            self.mode_buttons[mode].setAccessibleName(self._text(source))
        hint = self._text("Select to inspect; double-click to edit.")
        self.hint_lbl.setText(self._text("View: {view} | {hint}", view=snapshot.slice_note, hint=hint)
                              if snapshot and snapshot.slice_note else hint)
        if snapshot:
            self.shape_lbl.setText(self._text("{name} shape={shape}", name=snapshot.name, shape=snapshot.full_shape))
            view = f"{snapshot.name}{snapshot.slice_note}" if snapshot.slice_note else self._text("Full array")
            self.shape_lbl.setToolTip(self._text("Full shape: {shape}; current view: {view}; dtype/format: {format}",
                                              shape=snapshot.full_shape, view=view, format=snapshot.format_str))
        if self._error_source:
            self.error_lbl.setText(self._text(self._error_source[0], **self._error_source[1]))
        self.model._refresh_display(cfg)
        self._arrange_toolbar()

    def apply_appearance(self, cfg):
        self.palette = get_palette(cfg.theme)
        for button, icon, tooltip in (
            (self.copy_btn, "copy_table", "Copy entire matrix as TSV (up to 100,000 cells)"),
            (self.selection_copy_btn, "copy", "Copy selected cells as TSV (Ctrl+C)"),
            (self.paste_btn, "paste", "Paste TSV at the selected cell (Ctrl+V)"),
            (self.preview_btn, "preview", "Preview format conversion for the whole array"),
            (self.stats_btn, "statistics", "Show statistics for the selected cells"),
        ):
            style_icon_button(button, icon, t(tooltip, cfg.language), self.palette.text_secondary, cfg)
        self.table.verticalHeader().setDefaultSectionSize(scaled_px(28, cfg))
        self.table.horizontalHeader().setDefaultSectionSize(scaled_px(140, cfg))
        self._mode_columns = None
        self.retranslate_ui(cfg)
        self._arrange_toolbar()
        # Appearance callers may install the new stylesheet after this method;
        # measure the final font once Qt has applied those metrics.
        QTimer.singleShot(0, self._arrange_toolbar)

    def _on_format_edited(self):
        text = self.fmt_edit.text().strip()
        current = (FormatEngine.editable(self._last_snapshot.spec, self.controller.config)
                   if self._last_snapshot and self._last_snapshot.is_fxp else "")
        if text and self._last_snapshot and text != current:
            self.controller.change_variable_format(self.var_name, text)

    def render_snapshot(self, snapshot):
        if (self._last_snapshot is not None and
                (snapshot.slice_indices, snapshot.full_shape, snapshot.format_str) !=
                (self._last_snapshot.slice_indices, self._last_snapshot.full_shape, self._last_snapshot.format_str)):
            self._failed_edit = None
            self.retry_edit.hide()
            self._error("")
        self._last_snapshot = snapshot
        self.fmt_lbl.setText(self._text("Array format:" if snapshot.is_fxp else "Dtype:"))
        self._set_interaction_enabled(self.controller.is_available)
        self.shape_lbl.setText(self._text("{name} shape={shape}", name=snapshot.name, shape=snapshot.full_shape))
        slice_text = f"{snapshot.name}{snapshot.slice_note}" if snapshot.slice_note else self._text("Full array")
        self.shape_lbl.setToolTip(
            self._text("Full shape: {shape}; current view: {view}; dtype/format: {format}",
                       shape=snapshot.full_shape, view=slice_text, format=snapshot.format_str))
        self.slice_edit.setVisible(bool(snapshot.slice_indices))
        if not self.slice_edit.hasFocus() and not self.slice_edit.isModified():
            self.slice_edit.setText(", ".join(map(str, snapshot.slice_indices)))
        hint = self._text("Select to inspect; double-click to edit.")
        self.hint_lbl.setText(self._text("View: {view} | {hint}", view=snapshot.slice_note, hint=hint) if snapshot.slice_note else hint)
        if not self.fmt_edit.hasFocus() and not self.fmt_edit.isModified():
            self.fmt_edit.setText(FormatEngine.editable(snapshot.spec, self.controller.config)
                                  if snapshot.is_fxp else snapshot.format_str)
        for mode, button in self.mode_buttons.items():
            button.setEnabled(mode == "real" or snapshot.is_fxp)
        if not snapshot.is_fxp:
            self.model.mode = "real"
            self.mode_buttons["real"].setChecked(True)
        self.model.set_snapshot(snapshot, self.controller.revision)
        self._arrange_toolbar()
        if snapshot.shape[0] and snapshot.shape[1]:
            with QSignalBlocker(self.table.selectionModel()):
                current = self.table.currentIndex()
                if current.row() != snapshot.selected_row or current.column() != snapshot.selected_col:
                    self.table.setCurrentIndex(self.model.index(snapshot.selected_row, snapshot.selected_col))

    def _on_selection(self, current, previous):
        if current.isValid():
            self.controller.select_variable(self.var_name, (current.row(), current.column()))
