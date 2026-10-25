"""Workspace variable table widget with draggable columns, header column chooser, and context menu."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..controller import CalcController
from ..core.config import ALL_WORKSPACE_COLUMNS
from ..core.session import MAX_SATURATION_CHECK_CELLS, WorkspaceRowSnapshot
from .i18n import UiText, notify, tr
from .icons import make_icon, style_icon_button
from .numeric_display import format_numeric_text
from .theme import get_palette, scaled_px, set_role


class WorkspaceWidget(QFrame):
    """Top-right Workspace table displaying user variables."""

    inspect_requested = Signal(str, str)  # variable name, "value" or "format"

    def __init__(
        self,
        controller: CalcController,
        on_ensure_inspector_visible: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PanelFrame")
        self.controller = controller
        self.copy = UiText(controller.config.language, keep_english_terms=controller.config.keep_english_terms)
        self.palette = get_palette(controller.config.theme)
        self._on_ensure_inspector_visible = on_ensure_inspector_visible
        self._rows: list[WorkspaceRowSnapshot] = []
        self._visible_rows: list[WorkspaceRowSnapshot] = []
        self._updating: bool = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header Bar
        header = QFrame(self)
        header.setObjectName("PanelHeader")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(10, 5, 8, 5)
        h_layout.setSpacing(4)

        self.title_lbl = QLabel(tr("workspace", controller.config.language, keep_english_terms=self.controller.config.keep_english_terms))
        self.title_lbl.setObjectName("PanelTitle")
        h_layout.addWidget(self.title_lbl)
        h_layout.addStretch(1)

        self.open_array_btn = QToolButton(header)
        self.copy.bind(self.open_array_btn, "Open array")
        self.copy.bind(self.open_array_btn, "Open the selected array, or double-click its Workspace row", "setToolTip")
        self.open_array_btn.clicked.connect(self._open_selected_array)
        self.open_array_btn.hide()
        h_layout.addWidget(self.open_array_btn)

        # Column chooser icon button
        self.cols_btn = QToolButton(header)
        self.cols_btn.setIcon(make_icon("columns", self.palette.text_secondary))
        self.cols_btn.setToolTip(
            tr("select_columns_tooltip", controller.config.language, keep_english_terms=self.controller.config.keep_english_terms)
        )
        self.cols_btn.clicked.connect(self._show_columns_menu_from_button)
        h_layout.addWidget(self.cols_btn)

        # Clear workspace trash icon button
        self.clear_btn = QToolButton(header)
        self.clear_btn.setIcon(make_icon("trash", self.palette.text_secondary))
        self.clear_btn.setToolTip(
            tr("clear_workspace_tooltip", controller.config.language, keep_english_terms=self.controller.config.keep_english_terms)
        )
        self.clear_btn.clicked.connect(self._confirm_clear_workspace)
        h_layout.addWidget(self.clear_btn)

        layout.addWidget(header)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(8, 5, 8, 5)
        self.name_filter = QLineEdit(self)
        set_role(self.name_filter, "search")
        self.name_filter.setMinimumWidth(0)
        self.name_filter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.copy.bind(self.name_filter, "Filter variable names", "setPlaceholderText")
        self.name_filter.setClearButtonEnabled(True)
        self.type_filter = QComboBox(self)
        self.type_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.type_filter.setMinimumContentsLength(1)
        for title, kind in (("All types", "all"), ("Arrays", "array"),
                            ("Scalars", "scalar"), ("FXP", "fxp"),
                            ("Python / NumPy", "native")):
            self.type_filter.addItem(title, kind)
        self.filter_count = QLabel(self)
        set_role(self.filter_count, "secondary")
        filter_row.addWidget(self.name_filter, 1)
        filter_row.addWidget(self.type_filter)
        filter_row.setSpacing(8)
        filter_row.addWidget(self.filter_count)
        layout.addLayout(filter_row)
        self.name_filter.textChanged.connect(self._apply_filter)
        self.type_filter.currentIndexChanged.connect(self._apply_filter)

        # Table
        self.table = QTableWidget(self)
        self.table.setColumnCount(len(ALL_WORKSPACE_COLUMNS))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)

        h_header = self.table.horizontalHeader()
        h_header.setSectionsMovable(True)
        h_header.setStretchLastSection(True)
        h_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h_header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        h_header.customContextMenuRequested.connect(self._show_header_context_menu)

        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_row_context_menu)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)

        layout.addWidget(self.table, 1)

        # Keyboard shortcuts for Del and F2
        del_sc = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.table)
        del_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        del_sc.activated.connect(self._delete_selected_row)

        f2_sc = QShortcut(QKeySequence(Qt.Key.Key_F2), self.table)
        f2_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        f2_sc.activated.connect(self._rename_selected_row)

        self._setup_columns()
        self.controller.workspace_changed.connect(self.render_rows)
        self.controller.config_changed.connect(self._on_config_changed)
        self.retranslate_ui(controller.config)

    def apply_appearance(self, cfg):
        self.palette = get_palette(cfg.theme)
        self.table.verticalHeader().setDefaultSectionSize(scaled_px(28, cfg))
        self.retranslate_ui(cfg)

    def retranslate_ui(self, cfg):
        self.copy.refresh(cfg.language, keep_english_terms=cfg.keep_english_terms)
        self.title_lbl.setText(tr("workspace", cfg.language, keep_english_terms=self.controller.config.keep_english_terms))
        for index, source in enumerate(("All types", "Arrays", "Scalars", "FXP", "Python / NumPy")):
            self.type_filter.setItemText(index, self.copy(source))
        self._setup_columns()
        for button, icon, tooltip in (
            (self.open_array_btn, "open_array", "Open array editor\nOpen the selected array, or double-click its Workspace row."),
            (self.cols_btn, "columns", tr("select_columns_tooltip", cfg.language, keep_english_terms=self.controller.config.keep_english_terms)),
            (self.clear_btn, "trash", tr("clear_workspace_tooltip", cfg.language, keep_english_terms=self.controller.config.keep_english_terms)),
        ):
            style_icon_button(button, icon, self.copy(tooltip), self.palette.text_secondary, cfg,
                              label=self.copy("Columns") if button is self.cols_btn else None)
        self._update_filter_count()
        self.type_filter.setAccessibleName(self.copy("All types"))
        self.name_filter.setAccessibleName(self.copy("Filter variable names"))
        QTimer.singleShot(0, self._size_filter_controls)
        # A terminology-only refresh changes labels, not workspace data or
        # selection. Rebuild cells only when their displayed copy can change.
        key = (cfg.language, cfg.numeric_display, cfg.numeric_precision)
        if key != getattr(self, "_render_config_key", None):
            self._render_config_key = key
            self.render_rows(self._rows)

    def _size_filter_controls(self):
        width = self.type_filter.fontMetrics().horizontalAdvance(self.type_filter.currentText())
        self.type_filter.setFixedWidth(width + scaled_px(30, self.controller.config))
        height = max(self.name_filter.sizeHint().height(), self.type_filter.sizeHint().height())
        for control in (self.name_filter, self.type_filter):
            control.setFixedHeight(height)

    def _update_filter_count(self):
        self.filter_count.setText(self.copy("{visible} / {total} shown",
            visible=len(self._visible_rows), total=len(self._rows)))
        selected = self.controller.selected_var
        hidden = selected and any(row.name == selected for row in self._rows) and not any(
            row.name == selected for row in self._visible_rows)
        self.filter_count.setToolTip(self.copy("The selected variable is hidden by the filter; Inspector keeps its selection."
                                    if hidden else "Visible / total variables"))

    def _open_selected_array(self):
        snap = self._selected_row_snapshot()
        if snap and snap.is_array:
            if self._on_ensure_inspector_visible:
                self._on_ensure_inspector_visible()
            self.controller.open_matrix_editor(snap.name)

    def _setup_columns(self) -> None:
        lang = self.controller.config.language
        visible = set(self.controller.config.visible_columns)
        for idx, col_id in enumerate(ALL_WORKSPACE_COLUMNS):
            item = QTableWidgetItem(tr(f"col_{col_id}", lang, keep_english_terms=self.controller.config.keep_english_terms))
            item.setToolTip(tr(f"tip_{col_id}", lang, keep_english_terms=self.controller.config.keep_english_terms))
            item.setData(Qt.ItemDataRole.UserRole, col_id)
            self.table.setHorizontalHeaderItem(idx, item)
            self.table.setColumnHidden(idx, col_id not in visible)

    def _on_config_changed(self, cfg) -> None:
        self.retranslate_ui(cfg)

    def render_rows(self, rows: list[WorkspaceRowSnapshot]) -> None:
        self._rows = list(rows)
        self._apply_filter()

    def _apply_filter(self, *_args) -> None:
        QTimer.singleShot(0, self._size_filter_controls)
        query = self.name_filter.text().casefold()
        kind = self.type_filter.currentData()
        self._visible_rows = [row for row in self._rows
                              if query in row.name.casefold()
                              and (kind == "all"
                                   or (kind == "array" and row.is_array)
                                   or (kind == "scalar" and not row.is_array)
                                   or (kind == "fxp" and row.is_fxp)
                                   or (kind == "native" and not row.is_fxp))]
        self._update_filter_count()
        vertical = self.table.verticalScrollBar().value()
        horizontal = self.table.horizontalScrollBar().value()
        current_column = max(0, self.table.currentColumn())
        self._updating = True
        try:
            self.table.setRowCount(len(self._visible_rows))
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            selected_row_idx = -1
            for r_idx, row in enumerate(self._visible_rows):
                if row.name == self.controller.selected_var:
                    selected_row_idx = r_idx
                for c_idx, col_id in enumerate(ALL_WORKSPACE_COLUMNS):
                    source_text = row.get_column(col_id)
                    text = format_numeric_text(source_text, self.controller.config) if col_id == "value" else source_text
                    if row.is_fxp and row.is_array and col_id == "value":
                        if row.is_saturated is True:
                            text = f"[SAT] {text}"
                        elif row.is_saturated is None:
                            text = f"[SAT?] {text}"
                    item = QTableWidgetItem(text)
                    if col_id == "format":
                        hint = "\n" + self.copy("Double-click to convert / adjust format in Inspector.")
                    elif row.is_array:
                        hint = "\n" + self.copy("Double-click to open the array; click to inspect an element. Indices start at 0.")
                    elif col_id == "value":
                        hint = "\n" + self.copy("Double-click to edit the value in Inspector.")
                    else:
                        hint = ""
                    if row.is_fxp and row.is_array:
                        if row.is_saturated is True:
                            hint += "\n" + self.copy("SAT: At least one current cell is verified to match a saturated conversion.")
                        elif row.is_saturated is False:
                            hint += "\n" + self.copy("SAT: Every current cell's conversion source is verified; none was saturated.")
                        else:
                            hint += "\n" + self.copy("SAT?: Conversion sources have not been verified for the whole array; saturation status is unknown. Arrays larger than {limit} cells are not scanned automatically. Select a cell to inspect it.", limit=MAX_SATURATION_CHECK_CELLS)
                    item.setToolTip(f"{row.name} ({col_id}): {source_text}{hint}")
                    if col_id == "name":
                        item.setForeground(QColor(self.palette.accent))
                    elif col_id == "format" and row.is_fxp:
                        item.setForeground(QColor(self.palette.warning))
                    if row.is_saturated and col_id in ("value", "hex"):
                        item.setForeground(QColor(self.palette.danger))
                    self.table.setItem(r_idx, c_idx, item)

            if selected_row_idx >= 0:
                self.table.selectRow(selected_row_idx)
                self.table.setCurrentCell(selected_row_idx, current_column)
            self.open_array_btn.setVisible(selected_row_idx >= 0 and self._visible_rows[selected_row_idx].is_array)
            self.table.verticalScrollBar().setValue(vertical)
            self.table.horizontalScrollBar().setValue(horizontal)
        finally:
            self._updating = False

    def _selected_row_snapshot(self) -> WorkspaceRowSnapshot | None:
        row_idx = self.table.currentRow()
        if 0 <= row_idx < len(self._visible_rows):
            return self._visible_rows[row_idx]
        return None

    def _on_selection_changed(self) -> None:
        if self._updating:
            return
        snap = self._selected_row_snapshot()
        if snap is not None:
            self.controller.select_variable(snap.name)

    def _on_cell_clicked(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._visible_rows):
            self.controller.select_variable(self._visible_rows[row].name)

    def _on_cell_double_clicked(self, row: int, _col: int) -> None:
        if not (0 <= row < len(self._visible_rows)):
            return
        snap = self._visible_rows[row]
        if self._on_ensure_inspector_visible is not None:
            self._on_ensure_inspector_visible()
        column = ALL_WORKSPACE_COLUMNS[_col]
        if snap.is_array and column != "format":
            self.controller.open_matrix_editor(snap.name)
        else:
            self.controller.select_variable(snap.name)
            self.inspect_requested.emit(snap.name, "format" if column == "format" else "value")

    # ── Header Column Chooser Menu ────────────────────────────────────────

    def _show_columns_menu_from_button(self) -> None:
        pos = self.cols_btn.mapToGlobal(QPoint(0, self.cols_btn.height()))
        self._exec_columns_menu(pos)

    def _show_header_context_menu(self, pos: QPoint) -> None:
        global_pos = self.table.horizontalHeader().mapToGlobal(pos)
        self._exec_columns_menu(global_pos)

    def _exec_columns_menu(self, global_pos: QPoint) -> None:
        lang = self.controller.config.language
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        visible = set(self.controller.config.visible_columns)

        for col_id in ALL_WORKSPACE_COLUMNS:
            title = tr(f"col_{col_id}", lang, keep_english_terms=self.controller.config.keep_english_terms)
            tip = tr(f"tip_{col_id}", lang, keep_english_terms=self.controller.config.keep_english_terms)
            act = QAction(f"{title} — {tip}", menu)
            act.setCheckable(True)
            act.setChecked(col_id in visible)
            act.setToolTip(tip)
            if col_id == "name":
                act.setEnabled(False)
            act.triggered.connect(
                lambda checked, cid=col_id: self._toggle_column_visibility(cid, checked)
            )
            menu.addAction(act)

        menu.exec(global_pos)

    def _toggle_column_visibility(self, col_id: str, checked: bool) -> None:
        cols = list(self.controller.config.visible_columns)
        if checked and col_id not in cols:
            cols.append(col_id)
        elif not checked and col_id in cols and col_id != "name":
            cols.remove(col_id)
        self.controller.set_visible_columns(cols)
        self._setup_columns()

    # ── Variable Row Context Menu ─────────────────────────────────────────

    def _show_row_context_menu(self, pos: QPoint) -> None:
        item = self.table.itemAt(pos)
        if item is None:
            return
        row_idx = item.row()
        if not (0 <= row_idx < len(self._visible_rows)):
            return
        snap = self._visible_rows[row_idx]
        lang = self.controller.config.language

        menu = QMenu(self)
        if snap.is_array:
            act_open = menu.addAction(tr("ctx_open_matrix", lang, keep_english_terms=self.controller.config.keep_english_terms))
            act_open.triggered.connect(
                lambda: self.controller.open_matrix_editor(snap.name)
            )
        else:
            act_insp = menu.addAction(tr("ctx_inspect", lang, keep_english_terms=self.controller.config.keep_english_terms))
            act_insp.triggered.connect(lambda: self._inspect_variable_action(snap.name))

        menu.addSeparator()

        act_copy_real = menu.addAction(tr("ctx_copy_real", lang, keep_english_terms=self.controller.config.keep_english_terms))
        act_copy_real.triggered.connect(lambda: self._copy_text(snap.value_str))

        act_copy_hex = menu.addAction(tr("ctx_copy_hex", lang, keep_english_terms=self.controller.config.keep_english_terms))
        act_copy_hex.triggered.connect(lambda: self._copy_text(snap.hex_str))

        act_copy_s2 = menu.addAction(tr("ctx_copy_s2", lang, keep_english_terms=self.controller.config.keep_english_terms))
        act_copy_s2.triggered.connect(lambda: self._copy_text(snap.s2_str))

        act_copy_bin = menu.addAction(tr("ctx_copy_bin", lang, keep_english_terms=self.controller.config.keep_english_terms))
        act_copy_bin.triggered.connect(lambda: self._copy_text(snap.bin_str))

        menu.addSeparator()

        act_convert = menu.addAction(self.copy("Convert / adjust format in Inspector…"))
        act_convert.triggered.connect(lambda: self._navigate_conversion(snap.name))
        menu.addSeparator()

        act_ren = menu.addAction(tr("ctx_rename", lang, keep_english_terms=self.controller.config.keep_english_terms))
        act_ren.triggered.connect(self._rename_selected_row)

        act_dup = menu.addAction(tr("ctx_duplicate", lang, keep_english_terms=self.controller.config.keep_english_terms))
        act_dup.triggered.connect(
            lambda: self.controller.duplicate_variable(snap.name)
        )

        act_del = menu.addAction(tr("ctx_delete", lang, keep_english_terms=self.controller.config.keep_english_terms))
        act_del.triggered.connect(self._delete_selected_row)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _inspect_variable_action(self, name: str) -> None:
        if self._on_ensure_inspector_visible is not None:
            self._on_ensure_inspector_visible()
        self.controller.select_variable(name)

    def _copy_text(self, text: str) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(text)

    def _navigate_conversion(self, name):
        self._inspect_variable_action(name)
        self.inspect_requested.emit(name, "format")

    def _rename_selected_row(self) -> None:
        snap = self._selected_row_snapshot()
        if snap is None:
            return
        new_name, ok = self._text_prompt("Rename Variable", self.copy("New name for '{name}':", name=snap.name), snap.name)
        if ok and new_name.strip():
            try:
                self.controller.rename_variable(snap.name, new_name.strip())
            except Exception as exc:
                notify(self, self.copy("Rename Error"), str(exc), self.copy.lang, warning=True)

    def _text_prompt(self, title, label, value):
        dialog = QInputDialog(self)
        dialog.setWindowTitle(self.copy(title))
        dialog.setLabelText(label)
        dialog.setTextValue(value)
        dialog.setOkButtonText(self.copy("OK"))
        dialog.setCancelButtonText(self.copy("Cancel"))
        return_value = dialog.exec()
        return dialog.textValue(), bool(return_value)

    def _delete_selected_row(self) -> None:
        snap = self._selected_row_snapshot()
        if snap is not None:
            self.controller.delete_variable(snap.name)

    def _confirm_clear_workspace(self) -> None:
        if not self._rows:
            return
        dialog = QMessageBox(QMessageBox.Icon.Question, self.copy("Clear Workspace"),
                             self.copy("Clear all Workspace variables?"),
                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        dialog.button(QMessageBox.StandardButton.Yes).setText(self.copy("Yes"))
        dialog.button(QMessageBox.StandardButton.No).setText(self.copy("No"))
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        ans = dialog.exec()
        if ans == QMessageBox.StandardButton.Yes:
            self.controller.clear_workspace()
