"""The options chain (centre column, Options mode).

An expiration selector, a Calls/Puts toggle and a strike ladder that is priced
live off the underlying's current price — as the spot ticks, every visible
contract re-prices in place. Clicking a strike emits :attr:`contractSelected`,
which the owner loads into the option ticket. The chain math is entirely local
(see :mod:`paper_trader.data.options_chain`), so it works with any data source.
"""

from __future__ import annotations

from datetime import date

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.options import OptionRight, days_to_expiry
from ...data.options_chain import OptionsChainService
from .. import theme
from ..tables import align_headers
from ..format import fmt_price

_COLUMNS = ["Strike", "Bid", "Mark", "Ask", "Delta", "IV", ""]


class OptionsChainView(QWidget):
    """Interactive, live-priced option chain for the active underlying."""

    contractSelected = pyqtSignal(object, object)  # (OptionContract, OptionQuote)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = OptionsChainService()
        self._underlying = ""
        self._price = 0.0
        self._expiry: date | None = None
        self._right = OptionRight.CALL
        self._selected_strike: float | None = None
        self._last_key: tuple | None = None
        self._pending_scroll: int | None = None
        self._exp_buttons: dict[str, QPushButton] = {}
        self._build()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # Header: underlying + expiry summary.
        self._header = QLabel("Options")
        self._header.setStyleSheet("font-size: 14px; font-weight: 700;")
        root.addWidget(self._header)

        # Expiration selector.
        self._exp_row = QHBoxLayout(); self._exp_row.setSpacing(6)
        self._exp_group = QButtonGroup(self); self._exp_group.setExclusive(True)
        exp_wrap = QWidget(); exp_wrap.setLayout(self._exp_row)
        root.addWidget(exp_wrap)

        # Calls / Puts toggle.
        cp_row = QHBoxLayout(); cp_row.setSpacing(6)
        self._cp_group = QButtonGroup(self)
        self._calls_btn = self._segment("Calls", checked=True)
        self._puts_btn = self._segment("Puts")
        self._calls_btn.clicked.connect(lambda: self._set_right(OptionRight.CALL))
        self._puts_btn.clicked.connect(lambda: self._set_right(OptionRight.PUT))
        for b in (self._calls_btn, self._puts_btn):
            self._cp_group.addButton(b); cp_row.addWidget(b)
        cp_row.addStretch(1)
        self._legend = QLabel(""); self._legend.setObjectName("Faint")
        self._legend.setStyleSheet("font-size: 11px;")
        cp_row.addWidget(self._legend)
        root.addLayout(cp_row)

        # Strike ladder.
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(_COLUMNS)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        align_headers(self._table)
        self._table.cellClicked.connect(self._on_row_clicked)
        root.addWidget(self._table, 1)

        self._empty = QLabel("Waiting for a live underlying price…")
        self._empty.setObjectName("Faint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty)
        self._table.hide()

    def _segment(self, text: str, checked: bool = False) -> QPushButton:
        b = QPushButton(text); b.setObjectName("Segment")
        b.setCheckable(True); b.setChecked(checked)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def set_underlying(self, symbol: str, price: float | None) -> None:
        symbol = symbol.upper()
        if symbol != self._underlying:
            self._underlying = symbol
            self._selected_strike = None
            self._last_key = None
        if price and price > 0:
            self._price = float(price)
        self._ensure_expirations()
        self._render()

    def set_price(self, price: float | None) -> None:
        if price and price > 0:
            self._price = float(price)
            self._render()

    def selected_right(self) -> OptionRight:
        return self._right

    # ------------------------------------------------------------------ #
    def _ensure_expirations(self) -> None:
        exps = self._service.expirations()
        keys = [e.isoformat() for e in exps]
        if list(self._exp_buttons.keys()) == keys:
            return
        # Rebuild the expiration button row.
        for b in list(self._exp_buttons.values()):
            self._exp_group.removeButton(b)
            b.setParent(None)
            b.deleteLater()
        self._exp_buttons.clear()
        while self._exp_row.count():
            item = self._exp_row.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        for e in exps:
            dte = days_to_expiry(e)
            btn = QPushButton(f"{e.strftime('%b %d')}\n{dte}d")
            btn.setObjectName("RangeTab"); btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, d=e: self._set_expiry(d))
            self._exp_group.addButton(btn)
            self._exp_row.addWidget(btn)
            self._exp_buttons[e.isoformat()] = btn
        self._exp_row.addStretch(1)
        if self._expiry not in exps:
            self._expiry = exps[0] if exps else None
        self._sync_expiry_buttons()

    def _sync_expiry_buttons(self) -> None:
        for key, btn in self._exp_buttons.items():
            btn.setChecked(self._expiry is not None and key == self._expiry.isoformat())

    def _set_expiry(self, expiry: date) -> None:
        self._expiry = expiry
        self._selected_strike = None
        self._last_key = None
        self._sync_expiry_buttons()
        self._render()

    def _set_right(self, right: OptionRight) -> None:
        self._right = right
        self._last_key = None
        self._render()

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _render(self) -> None:
        ready = bool(self._underlying) and self._price > 0 and self._expiry is not None
        self._table.setVisible(ready)
        self._empty.setVisible(not ready)
        if not ready:
            self._header.setText(self._underlying or "Options")
            return

        chain = self._service.chain(self._underlying, self._price, self._expiry)
        dte = days_to_expiry(self._expiry)
        self._header.setText(
            f"{self._underlying}  ·  {fmt_price(self._price)}   "
            f"<span style='color:{theme.muted_color()}'>"
            f"{self._expiry.strftime('%b %d, %Y')} · {dte} DTE</span>")
        self._header.setTextFormat(Qt.TextFormat.RichText)
        self._legend.setText("Shaded = in the money · ● = at the money")

        is_call = self._right is OptionRight.CALL
        key = (self._underlying, self._expiry.isoformat(), self._right.value,
               tuple(r.strike for r in chain.rows))
        rebuild = key != self._last_key
        if rebuild:
            self._table.setRowCount(len(chain.rows))
        self._last_key = key

        atm_row = 0
        best = None
        for row, cr in enumerate(chain.rows):
            quote = cr.call if is_call else cr.put
            itm = (cr.strike < self._price) if is_call else (cr.strike > self._price)
            is_atm = abs(cr.strike - chain.atm_strike) < 1e-6
            if best is None or abs(cr.strike - self._price) < best[1]:
                best = (row, abs(cr.strike - self._price)); atm_row = row

            self._set(row, 0, ("● " if is_atm else "") + fmt_price(cr.strike),
                      bold=True, right=False)
            self._set(row, 1, fmt_price(quote.bid), right=True)
            self._set(row, 2, fmt_price(quote.mark), right=True,
                      color=(theme.gain_color() if is_call else theme.loss_color()))
            self._set(row, 3, fmt_price(quote.ask), right=True)
            self._set(row, 4, f"{quote.delta:+.2f}", right=True, color=theme.muted_color())
            self._set(row, 5, f"{quote.iv*100:.0f}%", right=True, color=theme.muted_color())
            self._set(row, 6, "ITM" if itm else "", right=True,
                      color=theme.color("accent") if itm else None)
            # Subtle ITM shading across the row.
            self._shade(row, itm, is_atm)

        if rebuild and best is not None:
            # Defer the scroll until after the table has laid out its new rows,
            # so the at-the-money strikes land in the centre of the viewport.
            self._pending_scroll = atm_row
            QTimer.singleShot(0, self._apply_scroll)
        self._highlight_selected(chain)

    def _apply_scroll(self) -> None:
        row = self._pending_scroll
        self._pending_scroll = None
        if row is None or not (0 <= row < self._table.rowCount()):
            return
        item = self._table.item(row, 0)
        if item is not None:
            self._table.scrollToItem(
                item, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _highlight_selected(self, chain) -> None:
        if self._selected_strike is None:
            return
        for row, cr in enumerate(chain.rows):
            if abs(cr.strike - self._selected_strike) < 1e-6:
                self._table.selectRow(row)
                return

    def _shade(self, row: int, itm: bool, atm: bool) -> None:
        if atm:
            bg = QColor(theme.color("selection"))
        elif itm:
            bg = QColor(theme.color("hover"))
        else:
            bg = QColor(theme.color("panel"))
        for col in range(len(_COLUMNS)):
            it = self._table.item(row, col)
            if it is not None:
                it.setBackground(bg)

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
        item.setTextAlignment(
            (Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft)
            | Qt.AlignmentFlag.AlignVCenter)
        item.setForeground(QColor(color) if color else QColor(theme.color("text")))

    # ------------------------------------------------------------------ #
    def _on_row_clicked(self, row: int, _col: int) -> None:
        if not self._underlying or self._expiry is None or self._price <= 0:
            return
        chain = self._service.chain(self._underlying, self._price, self._expiry)
        if not (0 <= row < len(chain.rows)):
            return
        cr = chain.rows[row]
        is_call = self._right is OptionRight.CALL
        contract = cr.call_contract if is_call else cr.put_contract
        quote = cr.call if is_call else cr.put
        self._selected_strike = cr.strike
        self.contractSelected.emit(contract, quote)

    def current_quote_for(self, contract):
        """Re-price ``contract`` against the current spot (for live ticket updates)."""
        from ...core.options import price_contract
        if self._price <= 0:
            return None
        return price_contract(contract, self._price)
