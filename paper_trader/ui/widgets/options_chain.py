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
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.options import OptionRight, days_to_expiry
from ...data.options_chain import OptionsChainService
from .. import theme
from ..tables import fit_columns, style_table
from ..format import fmt_price
from .segments import segment_group

_COLUMNS = ["Strike", "Bid", "Mark", "Ask", "Delta", "IV", ""]
_DROP = (6, 5, 4)     # the ITM tag, IV, then delta go first on a narrow column
_EXPIRY_ROW_HEIGHT = 40


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
        root.setSpacing(10)

        # Header: underlying + expiry summary.
        self._header = QLabel("Options")
        self._header.setObjectName("H3")
        self._header.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(self._header)

        # Expiration selector: a strip of chips that scrolls sideways when the
        # column is too narrow for all of them.
        self._exp_row = QHBoxLayout()
        self._exp_row.setSpacing(8)
        self._exp_row.setContentsMargins(0, 0, 0, 0)
        self._exp_group = QButtonGroup(self)
        self._exp_group.setExclusive(True)
        exp_wrap = QWidget()
        exp_wrap.setObjectName("Clear")
        exp_wrap.setLayout(self._exp_row)
        exp_scroll = _SideScroll()
        exp_scroll.setObjectName("Clear")
        exp_scroll.setWidget(exp_wrap)
        exp_scroll.setWidgetResizable(True)
        exp_scroll.setFrameShape(QFrame.Shape.NoFrame)
        exp_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        exp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        exp_scroll.setFixedHeight(_EXPIRY_ROW_HEIGHT)
        exp_scroll.viewport().setObjectName("Clear")
        root.addWidget(exp_scroll)

        # Calls / Puts toggle.
        cp_row = QHBoxLayout(); cp_row.setSpacing(6)
        track, (self._calls_btn, self._puts_btn) = segment_group(("Calls", "Puts"))
        self._cp_group = track._segment_group
        self._calls_btn.clicked.connect(lambda: self._set_right(OptionRight.CALL))
        self._puts_btn.clicked.connect(lambda: self._set_right(OptionRight.PUT))
        cp_row.addWidget(track)
        cp_row.addStretch(1)
        self._legend = QLabel(""); self._legend.setObjectName("Faint")
        self._legend.setStyleSheet("font-size: 12px;")
        cp_row.addWidget(self._legend)
        root.addLayout(cp_row)

        # Strike ladder.
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        style_table(self._table)
        self._table.setCursor(Qt.CursorShape.PointingHandCursor)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(_COLUMNS)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._table.cellClicked.connect(self._on_row_clicked)
        root.addWidget(self._table, 1)

        self._empty = QLabel("Waiting for a live underlying price…")
        self._empty.setObjectName("Faint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty)
        self._table.hide()

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

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        fit_columns(self._table, _DROP)
        if self._pending_scroll is not None:
            QTimer.singleShot(0, self._apply_scroll)

    def refresh_theme(self) -> None:
        """Repaint the row shading and header in the new palette."""
        self._last_key = None
        self._render()

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
            btn = QPushButton(f"{e.strftime('%b')} {e.day}")
            btn.setObjectName("Chip"); btn.setCheckable(True)
            btn.setToolTip(f"{e.strftime('%A, %B %d, %Y')} · {dte} days to expiry")
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
            f"{self._underlying} options"
            f"<span style='color:{theme.muted_color()}; font-weight:600'>"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;Expires {self._expiry.strftime('%b %d, %Y')}"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;{dte} days</span>")
        self._legend.setText("Shaded rows are in the money  ·  ● at the money")

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
            self._shade(row, itm, is_atm, is_call)

        fit_columns(self._table, _DROP)
        if rebuild and best is not None:
            # Defer the scroll until after the table has laid out its new rows,
            # so the at-the-money strikes land in the centre of the viewport.
            self._pending_scroll = atm_row
            QTimer.singleShot(0, self._apply_scroll)
        self._highlight_selected(chain)

    def _apply_scroll(self) -> None:
        row = self._pending_scroll
        if row is None or not (0 <= row < self._table.rowCount()):
            self._pending_scroll = None
            return
        # The chain is built while it is still the hidden page of the centre
        # stack, when the viewport has no real height to centre in — the strike
        # then lands on the bottom edge. Hold the request until it is on screen
        # (showEvent / resizeEvent come back here).
        if not self._table.isVisible() or self._table.viewport().height() < 60:
            return
        self._pending_scroll = None
        # Centre the strike, but land the *top* edge on a row boundary: plain
        # centring leaves a sliver of a row clipped at both ends, which reads
        # as a rendering glitch. Rows clipped only at the bottom read as
        # "more below".
        row_h = self._table.verticalHeader().defaultSectionSize()
        view_h = self._table.viewport().height()
        above = max(0, round((view_h - row_h) / 2 / row_h))
        bar = self._table.verticalScrollBar()
        bar.setValue(min(bar.maximum(), max(0, row - above) * row_h))

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if self._pending_scroll is not None:
            QTimer.singleShot(0, self._apply_scroll)

    def _highlight_selected(self, chain) -> None:
        if self._selected_strike is None:
            return
        for row, cr in enumerate(chain.rows):
            if abs(cr.strike - self._selected_strike) < 1e-6:
                self._table.selectRow(row)
                return

    def _shade(self, row: int, itm: bool, atm: bool, is_call: bool) -> None:
        """At the money reads as a band; in the money gets a faint wash in the
        side's colour; everything else sits on the page."""
        if atm:
            bg = QBrush(QColor(theme.color("selection")))
        elif itm:
            bg = QBrush(QColor(theme.color("green_wash" if is_call else "red_wash")))
        else:
            bg = QBrush()
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
            item.setFont(theme.strong(item.font()))
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


class _SideScroll(QScrollArea):
    """A one-row strip that scrolls sideways: the wheel moves it left/right.

    The scrollbar stays hidden (a bar under a row of chips reads as a rule);
    trackpads scroll it natively, and this maps an ordinary mouse wheel too.
    """

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        bar = self.horizontalScrollBar()
        delta = event.angleDelta()
        step = delta.x() or delta.y()
        if bar.maximum() > 0 and step:
            bar.setValue(bar.value() - step)
            event.accept()
            return
        super().wheelEvent(event)
