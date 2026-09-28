"""The option order ticket (right column, Options mode).

The same lifted card as the stock ticket, for a single selected contract: Buy /
Sell header tabs, the contract as the card's title with its moneyness and days
to expiry, a contracts stepper, the live bid/ask/mark and Greeks, then the bold
estimated cost/credit with the collateral note for short sales, and a pill whose
label says whether you are opening or closing (Buy to Open, Sell to Close, …).
Buy is always green and Sell always red, as on the stock ticket.
It's a view plus light validation; the authoritative checks and the fill happen
in the engine via the emitted :class:`OptionOrderTicket`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.options import (
    CONTRACT_MULTIPLIER,
    OptionContract,
    OptionQuote,
    collateral_per_contract,
    days_to_expiry,
)
from .. import icons, theme
from ..format import fmt_money, fmt_price

_FIELD_HEIGHT = 38


@dataclass(slots=True)
class OptionOrderTicket:
    """A user's option order request, handed to the owner to execute."""

    contract: OptionContract
    side: str          # "BUY" | "SELL"
    quantity: int
    price: float       # intended fill price/share (ask for buys, bid for sells)


class OptionTicket(QFrame):
    orderRequested = pyqtSignal(object)  # OptionOrderTicket

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._contract: OptionContract | None = None
        self._quote: OptionQuote | None = None
        self._underlying_price = 0.0
        self._buying_power = 0.0
        self._position = 0.0     # signed contracts currently held
        self._side = "BUY"
        self._build()
        self._render()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- header: Buy / Sell ---------------------------------------------- #
        header = QHBoxLayout()
        header.setContentsMargins(22, 16, 22, 14)
        header.setSpacing(0)
        self._side_group = QButtonGroup(self)
        self._buy_toggle = self._header_tab("Buy", checked=True)
        self._sell_toggle = self._header_tab("Sell")
        self._buy_toggle.clicked.connect(lambda: self._set_side("BUY"))
        self._sell_toggle.clicked.connect(lambda: self._set_side("SELL"))
        for b in (self._buy_toggle, self._sell_toggle):
            self._side_group.addButton(b)
            header.addWidget(b)
        header.addStretch(1)
        root.addLayout(header)

        header_rule = QFrame()
        header_rule.setObjectName("CardRule")
        root.addWidget(header_rule)

        body = QVBoxLayout()
        body.setContentsMargins(22, 16, 22, 20)
        body.setSpacing(12)

        # -- the contract ---------------------------------------------------- #
        self._title = QLabel("Options")
        self._title.setObjectName("CardTitle")
        self._title.setWordWrap(True)
        body.addWidget(self._title)
        self._contract_label = QLabel("Select a contract from the chain to trade.")
        self._contract_label.setObjectName("CardNote")
        self._contract_label.setWordWrap(True)
        self._contract_label.setTextFormat(Qt.TextFormat.RichText)
        body.addWidget(self._contract_label)
        # Kept for callers that read the chips separately; merged into the note.
        self._chips = QLabel("")
        self._chips.hide()
        body.addSpacing(2)

        # -- contracts stepper + quote rows ---------------------------------- #
        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        grid.setHorizontalSpacing(10)
        grid.setColumnStretch(0, 1)
        r = 0
        grid.addWidget(self._row_label("Contracts"), r, 0)
        qty_cell = QHBoxLayout()
        qty_cell.setSpacing(6)
        qty_cell.setContentsMargins(0, 0, 0, 0)
        self._minus = QPushButton()
        self._minus.setObjectName("Stepper")
        self._plus = QPushButton()
        self._plus.setObjectName("Stepper")
        for b in (self._minus, self._plus):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(_FIELD_HEIGHT, _FIELD_HEIGHT)
            b.setIconSize(QSize(14, 14))
        self._minus.clicked.connect(lambda: self._bump(-1))
        self._plus.clicked.connect(lambda: self._bump(1))
        self._qty = QLineEdit("1")
        self._qty.setValidator(QIntValidator(1, 100000))
        self._qty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qty.setFixedSize(56, _FIELD_HEIGHT)
        self._qty.setFont(theme.tabular(self._qty.font()))
        self._qty.textChanged.connect(self._render)
        self._max_btn = QPushButton("Max")
        self._max_btn.setObjectName("Ghost")
        self._max_btn.setFixedSize(44, _FIELD_HEIGHT)
        self._max_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._max_btn.clicked.connect(self._fill_max)
        qty_cell.addWidget(self._minus)
        qty_cell.addWidget(self._qty)
        qty_cell.addWidget(self._plus)
        qty_cell.addWidget(self._max_btn)
        wrap = QWidget()
        wrap.setObjectName("Clear")
        wrap.setLayout(qty_cell)
        grid.addWidget(wrap, r, 1, Qt.AlignmentFlag.AlignRight); r += 1

        self._bid = self._value_row(grid, r, "Bid"); r += 1
        self._ask = self._value_row(grid, r, "Ask"); r += 1
        self._mark = self._value_row(grid, r, "Mark", bold=True); r += 1
        self._greeks = self._value_row(grid, r, "Δ  /  θ  /  IV"); r += 1
        body.addLayout(grid)

        rule = QFrame()
        rule.setObjectName("CardRule")
        body.addSpacing(2)
        body.addWidget(rule)
        body.addSpacing(2)

        est_row = QHBoxLayout()
        self._est_key = QLabel("Estimated cost")
        self._est_key.setObjectName("CardTotalKey")
        self._est_val = QLabel("—")
        self._est_val.setObjectName("CardTotalVal")
        self._est_val.setFont(theme.tabular(self._est_val.font()))
        self._est_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        est_row.addWidget(self._est_key)
        est_row.addStretch(1)
        est_row.addWidget(self._est_val)
        body.addLayout(est_row)

        self._collateral = QLabel("")
        self._collateral.setObjectName("CardNote")
        self._collateral.setWordWrap(True)
        body.addWidget(self._collateral)

        self._hint = QLabel("")
        self._hint.setObjectName("Hint")
        self._hint.setWordWrap(True)
        body.addWidget(self._hint)

        body.addSpacing(4)
        self._submit = QPushButton("Buy")
        self._submit.setObjectName("BuyButton")
        self._submit.setFixedHeight(theme.PILL_HEIGHT)
        self._submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._submit.clicked.connect(self._submit_order)
        body.addWidget(self._submit)
        self._paint_side()

        self._owned_label = QLabel("")
        self._owned_label.setObjectName("CardNote")
        self._owned_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self._owned_label)

        root.addLayout(body)
        root.addStretch(1)
        self.refresh_theme()

    # ------------------------------------------------------------------ #
    # Context updates
    # ------------------------------------------------------------------ #
    def set_contract(self, contract: OptionContract, quote: OptionQuote,
                     position_qty: float) -> None:
        self._contract = contract
        self._quote = quote
        self._position = position_qty
        self._render()

    def update_quote(self, quote: OptionQuote) -> None:
        """Re-price the currently selected contract (live tick)."""
        self._quote = quote
        self._render()

    def set_underlying_price(self, price: float | None) -> None:
        self._underlying_price = float(price or 0.0)
        self._render()

    def set_account(self, buying_power: float, position_qty: float) -> None:
        self._buying_power = buying_power
        self._position = position_qty
        self._render()

    def _paint_side(self) -> None:
        """Buy reads green and Sell red — the tabs and the pill alike."""
        theme.set_accent(self._buy_toggle, "up")
        theme.set_accent(self._sell_toggle, "down")
        theme.set_accent(self._submit, "up" if self._side == "BUY" else "down")

    def refresh_theme(self) -> None:
        self._minus.setIcon(icons.icon("minus", theme.color("text"), 14))
        self._plus.setIcon(icons.icon("plus", theme.color("text"), 14))
        self._render()

    def has_contract(self) -> bool:
        return self._contract is not None

    def set_busy(self, busy: bool) -> None:
        """Disable the ticket while an order is in flight at a remote broker."""
        if busy:
            self._submit.setEnabled(False)
            self._submit.setText("Submitting…")
        else:
            self._render()

    def clear_contract(self) -> None:
        self._contract = None
        self._quote = None
        self._render()

    # ------------------------------------------------------------------ #
    # Widget helpers
    # ------------------------------------------------------------------ #
    def _header_tab(self, text: str, checked: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("HeaderTab")
        b.setCheckable(True)
        b.setChecked(checked)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    @staticmethod
    def _row_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("CardLabel")
        return lbl

    def _value_row(self, grid: QGridLayout, row: int, label: str,
                   bold: bool = False) -> QLabel:
        grid.addWidget(self._row_label(label), row, 0)
        val = QLabel("—")
        val.setObjectName("CardValue" if bold else "CardLabel")
        val.setFont(theme.tabular(val.font()))
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(val, row, 1)
        return val

    def _qty_value(self) -> int:
        try:
            return max(0, int(self._qty.text() or "0"))
        except ValueError:
            return 0

    def _bump(self, delta: int) -> None:
        self._qty.setText(str(max(1, self._qty_value() + delta)))

    # -- state changes ------------------------------------------------- #
    def _set_side(self, side: str) -> None:
        self._side = side
        self._buy_toggle.setChecked(side == "BUY")
        self._sell_toggle.setChecked(side == "SELL")
        self._paint_side()
        self._render()

    def _fill_price(self) -> float | None:
        if self._quote is None:
            return None
        return self._quote.ask if self._side == "BUY" else self._quote.bid

    def _fill_max(self) -> None:
        if self._contract is None or self._quote is None:
            return
        is_buy = self._side == "BUY"
        # Closing an existing opposite position caps at that size.
        if is_buy and self._position < 0:
            self._qty.setText(str(int(abs(self._position)))); return
        if not is_buy and self._position > 0:
            self._qty.setText(str(int(self._position))); return
        # Opening: size by buying power (premium for longs, collateral for shorts).
        if is_buy:
            per = self._quote.ask * CONTRACT_MULTIPLIER
        else:
            per = collateral_per_contract(self._contract, self._underlying_price) \
                - self._quote.bid * CONTRACT_MULTIPLIER
        per = max(per, 1e-9)
        self._qty.setText(str(max(1, math.floor(self._buying_power / per))))

    # -- render -------------------------------------------------------- #
    def _action_label(self) -> str:
        is_buy = self._side == "BUY"
        if is_buy:
            return "Buy to Close" if self._position < 0 else "Buy to Open"
        return "Sell to Close" if self._position > 0 else "Sell to Open"

    def _render(self) -> None:
        has = self._contract is not None and self._quote is not None
        self._minus.setEnabled(has)
        self._plus.setEnabled(has)
        self._qty.setEnabled(has)
        self._max_btn.setEnabled(has)

        if not has:
            self._title.setText("No contract selected")
            self._contract_label.setText("Pick a strike from the chain to trade it here.")
            for w in (self._bid, self._ask, self._mark, self._greeks):
                w.setText("—")
            self._est_val.setText("—")
            self._collateral.setText("")
            self._collateral.hide()
            self._hint.setText("")
            self._hint.hide()
            self._owned_label.setText("")
            self._owned_label.hide()
            self._submit.setEnabled(False)
            self._submit.setText("Buy to Open" if self._side == "BUY" else "Sell to Open")
            return

        c, q = self._contract, self._quote
        action = self._action_label()
        self._title.setText(f"{c.underlying} {c.short_label}")

        # Expiry, days to expiry and moneyness, on one quiet line.
        mny = c.moneyness(self._underlying_price) if self._underlying_price else ""
        mny_color = {"ITM": theme.gain_color(), "OTM": theme.muted_color(),
                     "ATM": theme.color("accent")}.get(mny, theme.muted_color())
        dte = days_to_expiry(c.expiry)
        muted = theme.muted_color()
        parts = [f"<span style='color:{muted}'>Expires {c.expiry.strftime('%b %d, %Y')}"
                 f"&nbsp;·&nbsp;{dte} DTE</span>"]
        if mny:
            parts.append(f"<span style='color:{mny_color}'>●&nbsp;{mny}</span>")
        self._contract_label.setText("&nbsp;&nbsp;".join(parts))

        self._bid.setText(fmt_price(q.bid))
        self._ask.setText(fmt_price(q.ask))
        self._mark.setText(fmt_price(q.mark))
        self._greeks.setText(f"{q.delta:+.2f}  /  {q.theta:+.2f}  /  {q.iv*100:.0f}%")

        qty = self._qty_value()
        fill = self._fill_price() or 0.0
        est = qty * fill * CONTRACT_MULTIPLIER
        is_buy = self._side == "BUY"
        opening = (is_buy and self._position >= 0) or (not is_buy and self._position <= 0)
        credit = not is_buy
        self._est_key.setText("Estimated credit" if credit else "Estimated cost")
        self._est_val.setText(fmt_money(est))

        # Collateral note for opening a short.
        if not is_buy and opening:
            per_col = collateral_per_contract(c, self._underlying_price)
            self._collateral.setText(
                f"Collateral required: {fmt_money(qty * per_col)} "
                f"(cash-secured {c.right.short.lower()})")
        else:
            self._collateral.setText("")
        self._collateral.setVisible(bool(self._collateral.text()))

        # Validation.
        hint = ""
        valid = qty >= 1
        if qty < 1:
            valid = False
        elif is_buy and est > self._buying_power + 1e-6 and opening:
            valid = False; hint = "Not enough buying power."
        elif not is_buy and opening:
            need = qty * collateral_per_contract(c, self._underlying_price) - est
            if need > self._buying_power + 1e-6:
                valid = False; hint = "Not enough buying power for the collateral."
        elif is_buy and not opening and qty > abs(self._position) + 1e-9:
            valid = False; hint = f"You only hold {int(abs(self._position))} short contract(s)."
        elif not is_buy and not opening and qty > self._position + 1e-9:
            valid = False; hint = f"You only hold {int(self._position)} contract(s)."
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))
        self._submit.setEnabled(valid)
        self._submit.setText(action)

        if self._position:
            held = "long" if self._position > 0 else "short"
            self._owned_label.setText(f"You hold {int(abs(self._position))} {held} contract(s)")
        else:
            self._owned_label.setText("")
        self._owned_label.setVisible(bool(self._position))

    def _submit_order(self) -> None:
        if self._contract is None or self._quote is None:
            return
        qty = self._qty_value()
        fill = self._fill_price()
        if qty < 1 or fill is None:
            return
        self.orderRequested.emit(OptionOrderTicket(
            contract=self._contract, side=self._side, quantity=qty, price=fill))
