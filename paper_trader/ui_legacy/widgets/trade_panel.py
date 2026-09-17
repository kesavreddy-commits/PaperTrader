"""The order ticket (right column) — the pre-rework card.

Buy/Sell header, a Market/Limit segmented pair, an "Invest in" Shares/Dollars
selector, the amount field with Max, live Market Price and Commissions rows, a
bold Estimated Cost and a green/red pill action button. A view plus light
client-side validation; the authoritative checks and execution happen in the
broker/engine via the emitted :class:`OrderTicket`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...ui import theme
from ...ui.format import fmt_money, fmt_price, fmt_shares


@dataclass(slots=True)
class OrderTicket:
    """A user's order request, handed to the owner to execute."""

    side: str        # "BUY" | "SELL"
    order_type: str  # "MARKET" | "LIMIT"
    mode: str        # "SHARES" | "DOLLARS"
    value: float     # shares or dollars, per `mode`
    limit_price: float | None = None
    extended_hours: bool = False  # route a limit order to the pre/after-hours session


class TradePanel(QWidget):
    orderRequested = pyqtSignal(object)  # OrderTicket

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._symbol = ""
        self._price: float | None = None
        self._cash = 0.0
        self._owned = 0.0
        self._side = "BUY"
        self._type = "MARKET"
        self._mode = "SHARES"
        self._ext_supported = False   # only the Alpaca account can trade extended hours
        self._session = ""            # live market session ("PRE"/"POST"/…)
        self._build()
        self._recompute()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(12)

        self._title = QLabel("Buy")
        self._title.setObjectName("H2")
        root.addWidget(self._title)

        # Buy / Sell toggle
        self._side_group = QButtonGroup(self)
        side_row = QHBoxLayout(); side_row.setSpacing(6)
        self._buy_toggle = self._segment("Buy", checked=True)
        self._sell_toggle = self._segment("Sell")
        self._buy_toggle.clicked.connect(lambda: self._set_side("BUY"))
        self._sell_toggle.clicked.connect(lambda: self._set_side("SELL"))
        for b in (self._buy_toggle, self._sell_toggle):
            self._side_group.addButton(b); side_row.addWidget(b)
        root.addLayout(side_row)

        # Market / Limit toggle
        self._type_group = QButtonGroup(self)
        type_row = QHBoxLayout(); type_row.setSpacing(6)
        self._market_toggle = self._segment("Market", checked=True)
        self._limit_toggle = self._segment("Limit")
        self._market_toggle.clicked.connect(lambda: self._set_type("MARKET"))
        self._limit_toggle.clicked.connect(lambda: self._set_type("LIMIT"))
        for b in (self._market_toggle, self._limit_toggle):
            self._type_group.addButton(b); type_row.addWidget(b)
        root.addLayout(type_row)

        # Robinhood-style rows
        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        grid.setHorizontalSpacing(10)
        grid.setColumnStretch(0, 1)
        r = 0

        grid.addWidget(self._row_label("Invest in"), r, 0)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Shares", "Dollars"])
        self._mode_combo.currentTextChanged.connect(
            lambda t: self._set_mode("DOLLARS" if t == "Dollars" else "SHARES"))
        self._mode_combo.setFixedWidth(130)
        grid.addWidget(self._mode_combo, r, 1); r += 1

        self._amount_label = self._row_label("Shares")
        grid.addWidget(self._amount_label, r, 0)
        amount_cell = QHBoxLayout(); amount_cell.setSpacing(6)
        self._amount = QLineEdit()
        self._amount.setPlaceholderText("0")
        self._amount.setValidator(_positive_validator())
        self._amount.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._amount.textChanged.connect(self._recompute)
        self._max_btn = QPushButton("Max")
        self._max_btn.setObjectName("RangeTab")
        self._max_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._max_btn.clicked.connect(self._fill_max)
        amount_cell.addWidget(self._amount, 1); amount_cell.addWidget(self._max_btn)
        amount_wrap = QWidget(); amount_wrap.setLayout(amount_cell)
        amount_wrap.setFixedWidth(150)
        grid.addWidget(amount_wrap, r, 1); r += 1

        self._limit_label = self._row_label("Limit Price")
        grid.addWidget(self._limit_label, r, 0)
        self._limit_price = QLineEdit()
        self._limit_price.setPlaceholderText("0.00")
        self._limit_price.setValidator(_positive_validator())
        self._limit_price.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._limit_price.setFixedWidth(150)
        self._limit_price.textChanged.connect(self._recompute)
        grid.addWidget(self._limit_price, r, 1); r += 1

        grid.addWidget(self._row_label("Market Price"), r, 0)
        self._market_price_val = self._row_value("—")
        grid.addWidget(self._market_price_val, r, 1); r += 1

        grid.addWidget(self._row_label("Commissions"), r, 0)
        grid.addWidget(self._row_value("$0.00"), r, 1); r += 1
        root.addLayout(grid)

        # Extended-hours (pre-market / after-hours) — Alpaca limit orders only.
        self._ext_hours = QCheckBox("Extended-hours order")
        self._ext_hours.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ext_hours.setToolTip(
            "Fill during pre-market (4:00–9:30 ET) or after-hours (4:00–8:00 ET).\n"
            "Extended-hours orders must be limit orders for whole shares.")
        self._ext_hours.toggled.connect(self._recompute)
        root.addWidget(self._ext_hours)
        self._session_hint = QLabel("")
        self._session_hint.setObjectName("Muted")
        self._session_hint.setStyleSheet("font-size: 11px;")
        root.addWidget(self._session_hint)
        self._ext_hours.hide()
        self._session_hint.hide()

        divider = QFrame(); divider.setObjectName("Divider")
        divider.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(divider)

        est_row = QHBoxLayout()
        self._est_key = QLabel("Estimated Cost")
        self._est_key.setStyleSheet("font-weight: 700;")
        self._est_val = QLabel("—")
        self._est_val.setStyleSheet("font-weight: 700;")
        self._est_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        est_row.addWidget(self._est_key); est_row.addStretch(1); est_row.addWidget(self._est_val)
        root.addLayout(est_row)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {theme.loss_color()}; font-size: 12px;")
        root.addWidget(self._hint)

        self._submit = QPushButton("Buy")
        self._submit.setObjectName("BuyButton")
        self._submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._submit.clicked.connect(self._submit_order)
        root.addWidget(self._submit)

        self._owned_label = QLabel("")
        self._owned_label.setObjectName("Muted")
        self._owned_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._owned_label.setStyleSheet("font-size: 12px;")
        root.addWidget(self._owned_label)

        root.addStretch(1)
        self._limit_label.hide(); self._limit_price.hide()

    # ------------------------------------------------------------------ #
    # Context updates
    # ------------------------------------------------------------------ #
    def set_symbol(self, symbol: str) -> None:
        self._symbol = symbol.upper()
        self._recompute()

    def set_market_price(self, price: float | None) -> None:
        self._price = price
        self._market_price_val.setText(fmt_price(price) if price else "—")
        self._recompute()

    def set_account(self, cash: float, owned_shares: float) -> None:
        self._cash = cash
        self._owned = owned_shares
        self._recompute()

    def set_extended_hours_supported(self, supported: bool) -> None:
        """Enable the extended-hours option (only the Alpaca account supports it)."""
        self._ext_supported = supported
        if not supported:
            self._ext_hours.setChecked(False)
        self._update_ext_visibility()

    def set_session(self, market_state: str) -> None:
        """Note the live session so the extended-hours hint can reflect it."""
        self._session = (market_state or "").upper()
        self._update_ext_visibility()

    def set_busy(self, busy: bool) -> None:
        self._submit.setEnabled(not busy)
        if busy:
            self._submit.setText("Submitting…")
        else:
            self._recompute()

    def clear_amount(self) -> None:
        self._amount.clear()

    # ------------------------------------------------------------------ #
    # Widget helpers
    # ------------------------------------------------------------------ #
    def _segment(self, text: str, checked: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("Segment")
        b.setCheckable(True)
        b.setChecked(checked)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    @staticmethod
    def _row_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("Muted")
        return lbl

    @staticmethod
    def _row_value(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 600;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    # -- state changes ------------------------------------------------- #
    def _set_side(self, side: str) -> None:
        self._side = side
        is_buy = side == "BUY"
        self._submit.setObjectName("BuyButton" if is_buy else "SellButton")
        self._submit.style().unpolish(self._submit)
        self._submit.style().polish(self._submit)
        self._recompute()

    def _set_type(self, order_type: str) -> None:
        self._type = order_type
        show_limit = order_type == "LIMIT"
        self._limit_label.setVisible(show_limit)
        self._limit_price.setVisible(show_limit)
        if show_limit and not self._limit_price.text() and self._price:
            self._limit_price.setText(f"{self._price:.2f}")
        self._update_ext_visibility()
        self._recompute()

    def _update_ext_visibility(self) -> None:
        show = self._ext_supported and self._type == "LIMIT"
        self._ext_hours.setVisible(show)
        extended = self._session in ("PRE", "POST")
        self._session_hint.setVisible(show and extended)
        if extended:
            label = "Pre-market" if self._session == "PRE" else "After-hours"
            self._session_hint.setText(f"● {label} session is open")
            self._session_hint.setStyleSheet(
                f"font-size: 11px; color: {theme.color('accent')};")

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._amount_label.setText("Amount ($)" if mode == "DOLLARS" else "Shares")
        self._recompute()

    def _reference_price(self) -> float | None:
        if self._type == "LIMIT":
            return _parse(self._limit_price.text())
        return self._price

    def _fill_max(self) -> None:
        price = self._reference_price()
        if not price or price <= 0:
            return
        if self._side == "BUY":
            if self._mode == "DOLLARS":
                self._amount.setText(f"{self._cash:.2f}")
            else:
                self._amount.setText(fmt_shares(math.floor(self._cash / price * 1e6) / 1e6))
        else:
            if self._mode == "DOLLARS":
                self._amount.setText(f"{self._owned * price:.2f}")
            else:
                self._amount.setText(fmt_shares(self._owned))

    # -- recompute ----------------------------------------------------- #
    def _recompute(self) -> None:
        is_buy = self._side == "BUY"
        label = "Buy" if is_buy else "Sell"
        self._title.setText(f"{label} {self._symbol}".strip())
        self._submit.setText(f"{label} {self._symbol}".strip() or label)
        self._est_key.setText("Estimated Cost" if is_buy else "Estimated Credit")
        self._owned_label.setText(
            f"You own {fmt_shares(self._owned)} shares" if self._owned else "")

        price = self._reference_price()
        amount = _parse(self._amount.text())
        hint = ""
        est = None
        valid = True

        if not self._symbol:
            valid = False
        elif self._type == "LIMIT" and (price is None or price <= 0):
            valid = False; hint = "Enter a limit price."
        elif price is None or price <= 0:
            valid = False; hint = "Waiting for a live price…"
        elif amount is None or amount <= 0:
            valid = False
        else:
            if self._mode == "DOLLARS":
                shares = amount / price; est = amount
            else:
                shares = amount; est = shares * price
            if is_buy and est > self._cash + 1e-6:
                valid = False; hint = "Not enough buying power."
            elif not is_buy and shares > self._owned + 1e-9:
                valid = False; hint = f"You only own {fmt_shares(self._owned)} shares."

        self._est_val.setText(fmt_money(est) if est is not None else "—")
        self._hint.setText(hint)
        self._submit.setEnabled(valid)

    def _submit_order(self) -> None:
        amount = _parse(self._amount.text())
        if amount is None or amount <= 0:
            return
        limit_price = _parse(self._limit_price.text()) if self._type == "LIMIT" else None
        ext = (self._type == "LIMIT" and self._ext_supported
               and self._ext_hours.isChecked())
        self.orderRequested.emit(OrderTicket(
            side=self._side, order_type=self._type, mode=self._mode,
            value=amount, limit_price=limit_price, extended_hours=ext,
        ))


def _positive_validator() -> QDoubleValidator:
    v = QDoubleValidator(0.0, 1e12, 6)
    v.setNotation(QDoubleValidator.Notation.StandardNotation)
    return v


def _parse(text: str) -> float | None:
    text = (text or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
