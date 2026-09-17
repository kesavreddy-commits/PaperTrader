"""Shared table chrome.

Qt centres header labels by default, which reads wrong above columns of figures:
the header floats in the middle of a stretched column while the values hug the
right edge. :func:`align_headers` puts each label over its own data.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidget

_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def align_headers(table: QTableWidget, left_columns: tuple[int, ...] = (0,)) -> None:
    """Left-align the label columns and right-align every numeric one."""
    for col in range(table.columnCount()):
        item = table.horizontalHeaderItem(col)
        if item is not None:
            item.setTextAlignment(_LEFT if col in left_columns else _RIGHT)
