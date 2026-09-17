"""The price header: symbol, name, live price, change, and day statistics.

The pre-rework layout: an identity row (symbol · name · market-state chip), then
price + change side by side, then a strip of day statistics underneath. Purely a
view — it receives :class:`Quote` objects and renders them.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ...data.models import Quote
from ...ui import theme
from ...ui.anim import ColorFlash, NumberRoller
from ...ui.format import (
    fmt_compact,
    fmt_price,
    fmt_signed_money,
    fmt_signed_pct,
    fmt_time,
)


class PriceHeader(QWidget):
    """Displays the active symbol's price and today's change."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._symbol = ""
        self._last_price: float | None = None
        self._build()
        self.show_placeholder()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 2, 4, 2)
        root.setSpacing(2)

        # -- identity row: SYMBOL · Name · market-state chip ---------------- #
        ident = QHBoxLayout()
        ident.setSpacing(10)
        self._symbol_label = QLabel("—")
        self._symbol_label.setObjectName("H2")
        self._name_label = QLabel("")
        self._name_label.setObjectName("Muted")
        self._state_chip = QLabel("")
        self._state_chip.setObjectName("Faint")
        ident.addWidget(self._symbol_label)
        ident.addWidget(self._name_label, 1)
        ident.addWidget(self._state_chip, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(ident)

        # -- price + change row -------------------------------------------- #
        price_row = QHBoxLayout()
        price_row.setSpacing(14)
        self._price_label = QLabel("—")
        self._price_label.setObjectName("H1")
        self._price_roller = NumberRoller(self._price_label, fmt_price, duration=420)
        self._price_flash = ColorFlash(self._price_label)
        self._change_label = QLabel("")
        self._change_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        price_row.addWidget(self._price_label, 0, Qt.AlignmentFlag.AlignBottom)
        price_row.addWidget(self._change_label, 0, Qt.AlignmentFlag.AlignBottom)
        price_row.addStretch(1)
        self._updated_label = QLabel("")
        self._updated_label.setObjectName("Faint")
        price_row.addWidget(self._updated_label, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(price_row)

        # -- day statistics ------------------------------------------------- #
        stats = QGridLayout()
        stats.setContentsMargins(0, 8, 0, 0)
        stats.setHorizontalSpacing(26)
        stats.setVerticalSpacing(2)
        self._stat_values: dict[str, QLabel] = {}
        for col, key in enumerate(("Open", "High", "Low", "Prev Close", "Volume")):
            title = QLabel(key.upper())
            title.setObjectName("Faint")
            title.setStyleSheet("font-size: 10px; letter-spacing: 0.5px;")
            value = QLabel("—")
            value.setStyleSheet("font-size: 13px; font-weight: 600;")
            stats.addWidget(title, 0, col)
            stats.addWidget(value, 1, col)
            self._stat_values[key] = value
        stats.setColumnStretch(5, 1)
        root.addLayout(stats)

    # ------------------------------------------------------------------ #
    def show_placeholder(self, symbol: str = "") -> None:
        """Reset to an empty/loading state (used while the first quote loads)."""
        self._symbol = symbol
        self._last_price = None
        self._symbol_label.setText(symbol or "—")
        self._name_label.setText("Loading…" if symbol else "Select a symbol")
        self._state_chip.setText("")
        self._price_roller.set_value(None)
        self._price_label.setStyleSheet("")
        self._price_label.setText("—")
        self._change_label.setText("")
        self._updated_label.setText("")
        for value in self._stat_values.values():
            value.setText("—")

    def update_quote(self, quote: Quote) -> None:
        self._symbol = quote.symbol
        self._symbol_label.setText(quote.symbol)
        name = quote.display_name if quote.display_name != quote.symbol else ""
        self._name_label.setText(name)

        state = _market_state_text(quote.market_state)
        self._state_chip.setText(state)

        # Flash the price green/red on a tick, then roll it to the new value.
        self._price_flash.set_base_style("", theme.color("text"))
        if self._last_price is not None and quote.price != self._last_price:
            self._price_flash.flash(
                theme.gain_color() if quote.price > self._last_price else theme.loss_color())
        self._last_price = quote.price
        self._price_roller.set_value(quote.price)

        color = theme.color_for(quote.change)
        self._change_label.setText(
            f"{fmt_signed_money(quote.change)}  ({fmt_signed_pct(quote.change_pct)})"
        )
        self._change_label.setStyleSheet(
            f"font-size: 15px; font-weight: 600; color: {color};"
        )
        self._updated_label.setText(f"Updated {fmt_time(quote.timestamp)}")

        self._stat_values["Open"].setText(fmt_price(quote.day_open))
        self._stat_values["High"].setText(fmt_price(quote.day_high))
        self._stat_values["Low"].setText(fmt_price(quote.day_low))
        self._stat_values["Prev Close"].setText(fmt_price(quote.previous_close))
        self._stat_values["Volume"].setText(
            fmt_compact(quote.volume) if quote.volume else "—"
        )

    @property
    def symbol(self) -> str:
        return self._symbol


def _market_state_text(state: str) -> str:
    mapping = {
        "REGULAR": "● Market Open",
        "PRE": "○ Pre-Market",
        "PREPRE": "○ Pre-Market",
        "POST": "○ After Hours",
        "POSTPOST": "○ After Hours",
        "CLOSED": "● Market Closed",
    }
    return mapping.get(state.upper(), "")
