"""The watchlist rail (left column).

A compact table of symbol / last price / % change that updates in place — no
full rebuilds on quote ticks, so there is no flicker and the selection survives.
Symbol *search* lives in the top nav bar; the owner validates a hit and calls
:meth:`add_symbol` here, which keeps every symbol in the list one the data
provider actually recognises.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...data.models import Quote
from .. import theme
from ..tables import align_headers
from ..format import fmt_price, fmt_signed_pct


class WatchlistPanel(QWidget):
    symbolSelected = pyqtSignal(str)       # user clicked a symbol to view it
    watchlistChanged = pyqtSignal(object)  # list[str] — persisted by the owner

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._symbols: list[str] = []
        self._row_of: dict[str, int] = {}
        self._active: str = ""
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 0)
        title = QLabel("WATCHLIST")
        title.setObjectName("SectionTitle")
        self._count = QLabel("")
        self._count.setObjectName("Faint")
        self._count.setStyleSheet("font-size: 11px;")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._count)
        root.addLayout(head)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Symbol", "Last", "Chg%"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)
        self._table.setWordWrap(False)
        self._table.setCursor(Qt.CursorShape.PointingHandCursor)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        align_headers(self._table)
        self._table.cellClicked.connect(self._on_row_clicked)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self._table, 1)

        self._hint = QLabel("Search above to add a symbol.")
        self._hint.setObjectName("Faint")
        self._hint.setStyleSheet("font-size: 11px;")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

    # ------------------------------------------------------------------ #
    # Watchlist data
    # ------------------------------------------------------------------ #
    def set_watchlist(self, symbols: list[str]) -> None:
        """Replace the whole list and rebuild rows (structure change)."""
        self._symbols = list(dict.fromkeys(s.upper() for s in symbols))
        self._row_of = {s: i for i, s in enumerate(self._symbols)}
        self._table.setRowCount(len(self._symbols))
        for row, sym in enumerate(self._symbols):
            self._set_cell(row, 0, sym, bold=True)
            self._set_cell(row, 1, "—", align_right=True)
            self._set_cell(row, 2, "—", align_right=True)
        self._count.setText(str(len(self._symbols)) if self._symbols else "")
        self._highlight_active()

    def update_quotes(self, quotes: dict[str, Quote]) -> None:
        """Update price/%-change cells in place for known symbols."""
        for sym, quote in quotes.items():
            row = self._row_of.get(sym)
            if row is None:
                continue
            self._set_cell(row, 1, fmt_price(quote.price), align_right=True)
            item = self._set_cell(row, 2, fmt_signed_pct(quote.change_pct), align_right=True)
            item.setForeground(QColor(theme.color_for(quote.change)))

    def set_active(self, symbol: str) -> None:
        self._active = symbol.upper()
        self._highlight_active()

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def contains(self, symbol: str) -> bool:
        return symbol.strip().upper() in self._row_of

    def add_symbol(self, symbol: str, *, select: bool = True) -> None:
        """Add a (already validated) symbol and optionally make it active."""
        symbol = symbol.strip().upper()
        if not symbol:
            return
        if symbol not in self._row_of:
            self._symbols.append(symbol)
            self.set_watchlist(self._symbols)
            self.watchlistChanged.emit(list(self._symbols))
        if select:
            self.set_active(symbol)
            self.symbolSelected.emit(symbol)

    def remove_symbol(self, symbol: str) -> None:
        symbol = symbol.strip().upper()
        if symbol in self._row_of:
            self._symbols.remove(symbol)
            self.set_watchlist(self._symbols)
            self.watchlistChanged.emit(list(self._symbols))

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _set_cell(self, row: int, col: int, text: str, *, bold: bool = False,
                  align_right: bool = False) -> QTableWidgetItem:
        item = self._table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, col, item)
        item.setText(text)
        if bold:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        if align_right:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _highlight_active(self) -> None:
        row = self._row_of.get(self._active)
        if row is not None:
            self._table.selectRow(row)

    # -- signal handlers ----------------------------------------------- #
    def _on_row_clicked(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._symbols):
            sym = self._symbols[row]
            self._active = sym
            self.symbolSelected.emit(sym)

    def _on_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._symbols):
            return
        sym = self._symbols[row]
        menu = QMenu(self)
        menu.addAction(f"View {sym}", lambda: self.symbolSelected.emit(sym))
        menu.addSeparator()
        menu.addAction(f"Remove {sym}", lambda: self.remove_symbol(sym))
        menu.exec(self._table.viewport().mapToGlobal(pos))
