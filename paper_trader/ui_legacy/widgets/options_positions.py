"""Open option positions (a tab in the bottom panel).

Renders the per-contract views from a :class:`PortfolioSnapshot`: signed
quantity, average premium, live mark, market value, unrealised P/L, delta and
days to expiry, with a one-click Close button per row. A pure view — the owner
executes the close through the broker.

Like the equity positions table, rows are rebuilt only when the *set* of held
contracts changes; on ordinary price ticks the cells are updated in place so the
Close buttons and any hover state survive.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.portfolio import OptionPositionView
from ...ui import theme
from ...ui.tables import align_headers
from ...ui.format import fmt_money, fmt_price, fmt_signed_money, fmt_signed_pct

_COLUMNS = ["Contract", "Qty", "Avg", "Mark", "Mkt Value",
            "Unrealized", "Return", "Δ", "DTE", ""]
_CLOSE_COL = len(_COLUMNS) - 1


class OptionsPositionsTable(QWidget):
    closeRequested = pyqtSignal(str)        # OCC symbol
    underlyingSelected = pyqtSignal(str)    # underlying ticker

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
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, _CLOSE_COL):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_CLOSE_COL, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_CLOSE_COL, 92)
        align_headers(self._table)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_menu)
        root.addWidget(self._table)

    # ------------------------------------------------------------------ #
    def update_positions(self, positions: list[OptionPositionView]) -> None:
        symbols = [p.symbol for p in positions]
        if symbols != self._symbols:
            self._rebuild(positions)
            self._symbols = symbols
        else:
            for row, pv in enumerate(positions):
                self._fill_cells(row, pv)

    def _rebuild(self, positions: list[OptionPositionView]) -> None:
        self._table.clearSpans()
        if not positions:
            self._table.setRowCount(1)
            for c in range(len(_COLUMNS)):
                self._table.removeCellWidget(0, c)
            item = QTableWidgetItem(
                "No option positions — switch to Options mode and pick a contract from the chain.")
            item.setForeground(QColor(theme.muted_color()))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(0, 0, item)
            self._table.setSpan(0, 0, 1, len(_COLUMNS))
            return
        self._table.setRowCount(len(positions))
        for row, pv in enumerate(positions):
            self._fill_cells(row, pv)
            self._table.setCellWidget(row, _CLOSE_COL, self._close_button(pv.symbol))

    # -- cell text (updated on every tick) ----------------------------- #
    def _fill_cells(self, row: int, pv: OptionPositionView) -> None:
        long = pv.quantity > 0
        self._set(row, 0, pv.description, bold=True)
        self._set(row, 1, f"{'+' if long else '−'}{int(abs(pv.quantity))}", right=True,
                  color=theme.gain_color() if long else theme.loss_color())
        self._set(row, 2, fmt_price(pv.avg_price), right=True)
        self._set(row, 3, fmt_price(pv.mark) if pv.priced else "—", right=True)
        self._set(row, 4, fmt_money(pv.market_value), right=True)
        self._set(row, 5, fmt_signed_money(pv.unrealized_pl), right=True,
                  color=theme.color_for(pv.unrealized_pl))
        self._set(row, 6, fmt_signed_pct(pv.unrealized_pl_pct), right=True,
                  color=theme.color_for(pv.unrealized_pl))
        self._set(row, 7, f"{pv.delta * pv.quantity:+.2f}", right=True, color=theme.muted_color())
        self._set(row, 8, f"{pv.dte}d", right=True,
                  color=theme.loss_color() if pv.dte <= 2 else theme.color("text"))

    def _close_button(self, occ: str) -> QWidget:
        """A Close button centred in a small wrapper so it never clips."""
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(2, 2, 2, 2)
        btn = QPushButton("Close")
        btn.setObjectName("Segment")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False, o=occ: self.closeRequested.emit(o))
        lay.addWidget(btn)
        return wrap

    def _set(self, row: int, col: int, text: str, *, bold: bool = False,
             right: bool = False, color: str | None = None) -> None:
        item = self._table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, col, item)
        item.setText(text)
        if bold:
            f = item.font(); f.setBold(True); item.setFont(f)
        if right:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setForeground(QColor(color) if color else QColor(theme.color("text")))

    # ------------------------------------------------------------------ #
    def _symbol_at(self, row: int) -> str | None:
        if self._symbols and 0 <= row < len(self._symbols):
            return self._symbols[row]
        return None

    def _on_menu(self, pos) -> None:
        occ = self._symbol_at(self._table.rowAt(pos.y()))
        if not occ:
            return
        from ...core.options import OptionContract
        underlying = OptionContract.parse(occ).underlying
        menu = QMenu(self)
        menu.addAction(f"View {underlying}", lambda: self.underlyingSelected.emit(underlying))
        menu.addAction("Close position", lambda: self.closeRequested.emit(occ))
        menu.exec(self._table.viewport().mapToGlobal(pos))
