"""Watchlist and symbol search (the left column of the pre-rework layout).

Back then the search field lived here rather than in a top nav: a debounced
field surfaces :attr:`searchRequested`, the owner performs the lookup off-thread
and calls :meth:`show_search_results`, and selecting a result adds the symbol to
the watchlist and makes it active. The table itself updates in place on quote
ticks (no full rebuilds, so there's no flicker or lost selection).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...data.models import Quote, SearchResult
from ...ui import theme
from ...ui.format import fmt_price, fmt_signed_pct


class WatchlistPanel(QWidget):
    symbolSelected = pyqtSignal(str)       # user clicked a symbol to view it
    watchlistChanged = pyqtSignal(object)  # list[str] — persisted by the owner
    searchRequested = pyqtSignal(str)      # debounced free-text query
    searchSubmitted = pyqtSignal(str)      # raw text submitted with Return

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._symbols: list[str] = []
        self._row_of: dict[str, int] = {}
        self._active: str = ""
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        title = QLabel("WATCHLIST")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search symbol or company…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_text)
        self._search.returnPressed.connect(self._on_search_return)
        root.addWidget(self._search)

        # Debounce so we don't fire a lookup on every keystroke.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(280)
        self._debounce.timeout.connect(self._emit_search)

        self._results = QListWidget()
        self._results.setMaximumHeight(200)
        self._results.itemClicked.connect(self._on_result_clicked)
        self._results.hide()
        root.addWidget(self._results)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Symbol", "Last", "Chg%"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)
        self._table.setWordWrap(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.cellClicked.connect(self._on_row_clicked)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self._table, 1)

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
        """Add a symbol; kept for parity with the current watchlist's API."""
        symbol = symbol.strip().upper()
        if not symbol:
            return
        if symbol not in self._row_of:
            self._symbols.append(symbol)
            self.set_watchlist(self._symbols)
            self.watchlistChanged.emit(list(self._symbols))
        self.clear_search()
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
    # Search
    # ------------------------------------------------------------------ #
    def show_search_results(self, results: list[SearchResult]) -> None:
        self._results.clear()
        if not results:
            self._results.hide()
            return
        for r in results:
            label = f"{r.symbol}"
            if r.name:
                label += f"   ·   {r.name}"
            meta = "  ".join(x for x in (r.type, r.exchange) if x)
            item = QListWidgetItem(f"{label}" + (f"    [{meta}]" if meta else ""))
            item.setData(Qt.ItemDataRole.UserRole, r.symbol)
            self._results.addItem(item)
        self._results.show()

    def clear_search(self) -> None:
        self._search.clear()
        self._results.clear()
        self._results.hide()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _set_cell(self, row: int, col: int, text: str, *, bold: bool = False,
                  align_right: bool = False) -> QTableWidgetItem:
        item = self._table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            flags = item.flags() & ~Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)
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
    def _on_search_text(self, text: str) -> None:
        if text.strip():
            self._debounce.start()
        else:
            self._results.hide()

    def _emit_search(self) -> None:
        query = self._search.text().strip()
        if query:
            self.searchRequested.emit(query)

    def _on_search_return(self) -> None:
        # Enter picks the first result; free text is validated by the owner.
        if self._results.isVisible() and self._results.count() > 0:
            sym = self._results.item(0).data(Qt.ItemDataRole.UserRole)
            self.add_symbol(sym)
        else:
            self.searchSubmitted.emit(self._search.text())

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        self.add_symbol(item.data(Qt.ItemDataRole.UserRole))

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
