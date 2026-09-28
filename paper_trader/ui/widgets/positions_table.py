"""Open-positions table (a tab in the bottom panel).

Renders the per-position views from a :class:`PortfolioSnapshot`: shares, average
cost, live price, market value, today's change, total unrealised P/L and weight.
Rows are rebuilt only when the *set* of held symbols changes; otherwise cells are
updated in place so frequent price ticks don't cause flicker or drop the user's
row selection.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.portfolio import PositionView
from .. import theme
from ..tables import fit_columns, style_table
from ..format import fmt_money, fmt_price, fmt_shares, fmt_signed_money, fmt_signed_pct

# Weight lives in the market-value cell's tooltip: nine columns overflowed the
# centre column at ordinary window sizes.
_COLUMNS = ["Symbol", "Shares", "Avg cost", "Price", "Market value",
            "Today", "Total return"]
# Hidden first when the column is too narrow for all of them.
_DROP = (2, 3, 1)


class PositionsTable(QWidget):
    symbolSelected = pyqtSignal(str)
    sellAllRequested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # None (not []) so the very first update — even an empty one —
        # rebuilds and shows the empty-state message.
        self._symbols: list[str] | None = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        style_table(self._table)
        self._table.setCursor(Qt.CursorShape.PointingHandCursor)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(_COLUMNS)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._table.cellClicked.connect(self._on_clicked)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_menu)
        root.addWidget(self._table)

    # ------------------------------------------------------------------ #
    def update_positions(self, positions: list[PositionView]) -> None:
        symbols = [p.symbol for p in positions]
        if symbols != self._symbols:
            self._rebuild(positions)
            self._symbols = symbols
        else:
            for row, pv in enumerate(positions):
                self._fill_row(row, pv)
        fit_columns(self._table, _DROP)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        fit_columns(self._table, _DROP)

    def _rebuild(self, positions: list[PositionView]) -> None:
        self._table.clearSpans()
        if not positions:
            self._table.setRowCount(1)
            item = QTableWidgetItem("No open positions — search a symbol and place a buy order.")
            item.setForeground(QColor(theme.muted_color()))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(0, 0, item)
            self._table.setSpan(0, 0, 1, len(_COLUMNS))
            return
        self._table.setRowCount(len(positions))
        for row, pv in enumerate(positions):
            self._fill_row(row, pv)

    def _fill_row(self, row: int, pv: PositionView) -> None:
        self._set(row, 0, pv.symbol, bold=True)
        self._set(row, 1, fmt_shares(pv.quantity), right=True)
        self._set(row, 2, fmt_price(pv.avg_cost), right=True)
        self._set(row, 3, fmt_price(pv.price) if pv.priced else "—", right=True)
        self._set(row, 4, fmt_money(pv.market_value), right=True)
        self._table.item(row, 4).setToolTip(f"{pv.weight * 100:.1f}% of your portfolio")

        if pv.day_change is None:
            self._set(row, 5, "—", right=True)
        else:
            self._set(
                row, 5,
                f"{fmt_signed_money(pv.day_change)} ({fmt_signed_pct(pv.day_change_pct)})",
                right=True, color=theme.color_for(pv.day_change),
            )
        self._set(row, 6,
                  f"{fmt_signed_money(pv.unrealized_pl)} ({fmt_signed_pct(pv.unrealized_pl_pct)})",
                  right=True, color=theme.color_for(pv.unrealized_pl))

    def _set(self, row: int, col: int, text: str, *, bold: bool = False,
             right: bool = False, color: str | None = None) -> None:
        item = self._table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, col, item)
        item.setText(text)
        # Set both every time: cells are reused across rebuilds (the empty-state
        # message included), so nothing may be left over from a previous use.
        f = item.font(); f.setBold(bold); item.setFont(f)
        item.setTextAlignment(
            (Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft)
            | Qt.AlignmentFlag.AlignVCenter)
        item.setForeground(QColor(color) if color else QColor(theme.color("text")))

    # ------------------------------------------------------------------ #
    def _symbol_at(self, row: int) -> str | None:
        if self._symbols and 0 <= row < len(self._symbols):
            return self._symbols[row]
        return None

    def _on_clicked(self, row: int, _col: int) -> None:
        sym = self._symbol_at(row)
        if sym:
            self.symbolSelected.emit(sym)

    def _on_menu(self, pos) -> None:
        sym = self._symbol_at(self._table.rowAt(pos.y()))
        if not sym:
            return
        menu = QMenu(self)
        menu.addAction(f"View {sym}", lambda: self.symbolSelected.emit(sym))
        menu.addAction(f"Sell all {sym}", lambda: self.sellAllRequested.emit(sym))
        menu.exec(self._table.viewport().mapToGlobal(pos))
