"""Transaction history and open-orders tables (bottom-panel tabs).

``HistoryTable`` is the immutable audit log of fills (newest first).
``OrdersTable`` lists limit orders with their status and a Cancel button for
resting ones. Both are pure views driven by the session's data.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.models import Order, OrderStatus, Side, Trade
from ...core.options import OptionContract
from .. import theme
from ..tables import align_headers
from ..format import (
    fmt_datetime,
    fmt_money,
    fmt_price,
    fmt_shares,
    fmt_signed_money,
)

_MAX_ROWS = 1000


class HistoryTable(QWidget):
    """The transaction log."""

    _COLUMNS = ["Time", "Symbol", "Side", "Type", "Qty", "Price", "Amount", "Realized P/L"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._table = _make_table(self._COLUMNS)
        root.addWidget(self._table)

    def update_trades(self, trades: list[Trade]) -> None:
        rows = list(reversed(trades))[:_MAX_ROWS]  # newest first
        if not rows:
            _show_empty(self._table, len(self._COLUMNS),
                        "No transactions yet — your fills will appear here.")
            return
        self._table.clearSpans()
        self._table.setRowCount(len(rows))
        for r, t in enumerate(rows):
            buy = t.side is Side.BUY
            _set(self._table, r, 0, fmt_datetime(t.timestamp))
            _set(self._table, r, 1, _trade_label(t), bold=True)
            _set(self._table, r, 2, t.side.value,
                 color=theme.gain_color() if buy else theme.loss_color())
            _set(self._table, r, 3, "Option" if t.is_option else t.order_type.value)
            _set(self._table, r, 4, fmt_shares(t.quantity), right=True)
            _set(self._table, r, 5, fmt_price(t.price), right=True)
            _set(self._table, r, 6, fmt_money(t.gross), right=True)
            # Any trade that booked P/L shows it — a short option bought back to
            # close is a BUY, and its result is just as real as a stock sale's.
            if t.realized_pl or t.side is Side.SELL:
                _set(self._table, r, 7, fmt_signed_money(t.realized_pl), right=True,
                     color=theme.color_for(t.realized_pl))
            else:
                _set(self._table, r, 7, "—", right=True)


class OrdersTable(QWidget):
    """Resting limit orders, with the ability to cancel pending ones."""

    cancelRequested = pyqtSignal(str)  # order id

    _COLUMNS = ["Created", "Symbol", "Side", "Size", "Limit", "Status", ""]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._table = _make_table(self._COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(
            len(self._COLUMNS) - 1, QHeaderView.ResizeMode.Fixed
        )
        self._table.setColumnWidth(len(self._COLUMNS) - 1, 90)
        root.addWidget(self._table)

    def update_orders(self, orders: list[Order]) -> None:
        # Pending first, then most recent others.
        ordered = sorted(
            orders,
            key=lambda o: (o.status is not OrderStatus.PENDING, -o.created_at.timestamp()),
        )
        if not ordered:
            _show_empty(self._table, len(self._COLUMNS),
                        "No limit orders — switch the order type to Limit to place one.")
            return
        self._table.clearSpans()
        self._table.setRowCount(len(ordered))
        for r, o in enumerate(ordered):
            buy = o.side is Side.BUY
            _set(self._table, r, 0, fmt_datetime(o.created_at))
            _set(self._table, r, 1, o.symbol, bold=True)
            _set(self._table, r, 2, o.side.value,
                 color=theme.gain_color() if buy else theme.loss_color())
            _set(self._table, r, 3, _order_size(o), right=True)
            _set(self._table, r, 4, fmt_price(o.limit_price), right=True)
            _set(self._table, r, 5, _status_text(o), color=_status_color(o))
            # Cancel button only for still-resting orders.
            if o.status is OrderStatus.PENDING:
                btn = QPushButton("Cancel")
                btn.setObjectName("Segment")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _=False, oid=o.id: self.cancelRequested.emit(oid))
                self._table.setCellWidget(r, 6, btn)
            else:
                self._table.removeCellWidget(r, 6)
                _set(self._table, r, 6, "")


# --------------------------------------------------------------------------- #
# Shared table helpers
# --------------------------------------------------------------------------- #
def _make_table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    for i in range(2, len(columns)):
        header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
    align_headers(table, left_columns=(0, 1, 2, 3))
    return table


def _set(table: QTableWidget, row: int, col: int, text: str, *, bold: bool = False,
         right: bool = False, color: str | None = None) -> None:
    item = table.item(row, col)
    if item is None:
        item = QTableWidgetItem()
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, col, item)
    item.setText(text)
    if bold:
        f = item.font(); f.setBold(True); item.setFont(f)
    if right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    item.setForeground(QColor(color) if color else QColor(theme.color("text")))


def _show_empty(table: QTableWidget, ncols: int, message: str) -> None:
    table.clearSpans()
    table.setRowCount(1)
    for c in range(ncols):
        table.removeCellWidget(0, c)
    item = QTableWidgetItem(message)
    item.setForeground(QColor(theme.muted_color()))
    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
    table.setItem(0, 0, item)
    table.setSpan(0, 0, 1, ncols)


def _trade_label(trade: Trade) -> str:
    """Friendly label: the contract description for options, else the ticker."""
    if trade.is_option:
        try:
            return OptionContract.parse(trade.symbol).description
        except ValueError:
            return trade.symbol
    return trade.symbol


def _order_size(order: Order) -> str:
    """Shares, or the dollar amount for a notional (dollar-sized) order."""
    if not order.quantity and order.notional:
        return fmt_money(order.notional)
    return fmt_shares(order.quantity)


def _status_text(order: Order) -> str:
    if order.status is OrderStatus.FILLED and order.fill_price is not None:
        return f"Filled @ {fmt_price(order.fill_price)}"
    if order.status is OrderStatus.REJECTED:
        return "Rejected"
    return order.status.value.capitalize()


def _status_color(order: Order) -> str:
    return {
        OrderStatus.PENDING: theme.color("accent"),
        OrderStatus.FILLED: theme.gain_color(),
        OrderStatus.CANCELLED: theme.muted_color(),
        OrderStatus.REJECTED: theme.loss_color(),
    }.get(order.status, theme.color("text"))
