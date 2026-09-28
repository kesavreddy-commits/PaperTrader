"""Shared table chrome.

Qt centres header labels by default, which reads wrong above columns of figures:
the header floats in the middle of a stretched column while the values hug the
right edge. :func:`align_headers` puts each label over its own data.

:func:`style_table` gives the current UI's tables one look: roomy rows, figures
that line up (tabular digits), no grid, and a hover that lights the whole row —
Qt on its own only lights the one cell under the cursor.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
)

from . import theme

_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

ROW_HEIGHT = 44
HEADER_HEIGHT = 36


def align_headers(table: QTableWidget, left_columns: tuple[int, ...] = (0,)) -> None:
    """Left-align the label columns and right-align every numeric one."""
    for col in range(table.columnCount()):
        item = table.horizontalHeaderItem(col)
        if item is not None:
            item.setTextAlignment(_LEFT if col in left_columns else _RIGHT)


def style_table(table: QTableWidget, left_columns: tuple[int, ...] = (0,)) -> None:
    """Apply the shared table look (see the module docstring)."""
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
    table.verticalHeader().setMinimumSectionSize(ROW_HEIGHT)
    header = table.horizontalHeader()
    header.setFixedHeight(HEADER_HEIGHT)
    header.setHighlightSections(False)
    header.setMinimumSectionSize(64)
    table.setShowGrid(False)
    table.setAlternatingRowColors(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setFont(theme.tabular(table.font()))
    table.setItemDelegate(RowHoverDelegate(table))
    align_headers(table, left_columns)


_CELL_PADDING = 28      # item + header text inset, both sides
_MEASURE_ROWS = 200     # enough to judge a column; cheap on every resize


def fit_columns(table: QTableWidget, droppable: Sequence[int]) -> None:
    """Hide ``droppable`` columns, in that order, until the others fit.

    A window narrower than a table's columns otherwise gets a horizontal
    scrollbar under the blotter; dropping the least important figures first
    (average cost before market value, say) keeps what matters on screen.
    Widths are measured from the text itself, so hidden columns can be
    judged too.
    """
    avail = table.viewport().width()
    if avail <= 0:
        return
    header = table.horizontalHeader()
    empty_state = table.rowCount() == 1 and table.columnSpan(0, 0) > 1
    head_fm = QFontMetrics(header.font())
    widths: dict[int, int] = {}
    for col in range(table.columnCount()):
        label = table.horizontalHeaderItem(col)
        width = head_fm.horizontalAdvance(label.text() if label else "") + _CELL_PADDING
        if header.sectionResizeMode(col) == QHeaderView.ResizeMode.Fixed:
            width = max(width, int(table.property(f"fixed_{col}") or 0))
        elif not empty_state:
            for row in range(min(table.rowCount(), _MEASURE_ROWS)):
                item = table.item(row, col)
                if item is not None and item.text():
                    fm = QFontMetrics(item.font() if item.font() != QFont() else table.font())
                    width = max(width, fm.horizontalAdvance(item.text()) + _CELL_PADDING)
        widths[col] = width
    total = sum(widths.values())
    hide: set[int] = set()
    for col in droppable:
        if total <= avail:
            break
        hide.add(col)
        total -= widths[col]
    for col in range(table.columnCount()):
        if header.isSectionHidden(col) != (col in hide):
            header.setSectionHidden(col, col in hide)


def set_fixed_column(table: QTableWidget, col: int, width: int) -> None:
    """Pin a column (e.g. a button column) to ``width`` and remember it."""
    table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
    table.setColumnWidth(col, width)
    table.setProperty(f"fixed_{col}", width)


class RowHoverDelegate(QStyledItemDelegate):
    """Tints the entire row under the cursor, not just the cell."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self._table = table
        self._row = -1
        table.setMouseTracking(True)
        table.viewport().installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if kind == QEvent.Type.MouseMove:
            row = self._table.rowAt(int(event.position().y()))
            if row != self._row:
                self._row = row
                self._table.viewport().update()
        elif kind == QEvent.Type.Leave and self._row != -1:
            self._row = -1
            self._table.viewport().update()
        return False

    def initStyleOption(self, option, index) -> None:  # noqa: N802 (Qt naming)
        super().initStyleOption(option, index)
        # Replaces the row's own background (e.g. a shaded in-the-money strike)
        # rather than painting under it, so every row visibly lights up.
        # Empty-state rows (one spanned message) don't: there is nothing to pick.
        if (index.row() == self._row
                and not option.state & QStyle.StateFlag.State_Selected
                and self._table.columnSpan(index.row(), 0) == 1):
            option.backgroundBrush = QBrush(QColor(theme.color("hover")))
