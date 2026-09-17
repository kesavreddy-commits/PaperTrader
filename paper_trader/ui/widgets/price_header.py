"""The price hero: company name, live price and today's change.

This is the piece that sets the tone of the page — the name and the price are
the two largest things on screen, with the change underneath in gain/loss colour
and the session state below that. Day statistics live in a separate
:class:`DayStatsCard` so the hero stays uncluttered and the numbers can sit in
their own panel under the chart.

Both are pure views: they receive :class:`Quote` objects and render them, and
never fetch anything.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ...data.models import Quote
from .. import theme
from ..anim import ColorFlash, NumberRoller
from ..format import (
    fmt_compact,
    fmt_price,
    fmt_signed_money,
    fmt_signed_pct,
    fmt_time,
)


class PriceHeader(QWidget):
    """Displays the active symbol's name, price and today's change."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._symbol = ""
        self._last_price: float | None = None
        self._build()
        self.show_placeholder()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        # -- name (+ ticker chip) ------------------------------------------ #
        ident = QHBoxLayout()
        ident.setSpacing(12)
        self._name_label = QLabel("—")
        self._name_label.setObjectName("Ticker")
        self._symbol_label = QLabel("")
        self._symbol_label.setObjectName("Muted")
        self._symbol_label.setStyleSheet("font-size: 13px; font-weight: 700;")
        ident.addWidget(self._name_label)
        ident.addWidget(self._symbol_label, 0, Qt.AlignmentFlag.AlignBottom)
        ident.addStretch(1)
        root.addLayout(ident)

        # -- price ---------------------------------------------------------- #
        self._price_label = QLabel("—")
        self._price_label.setObjectName("BigPrice")
        self._price_roller = NumberRoller(self._price_label, fmt_price, duration=420)
        self._price_flash = ColorFlash(self._price_label)
        root.addWidget(self._price_label)

        # -- change + session ----------------------------------------------- #
        self._change_label = QLabel("")
        self._change_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        root.addWidget(self._change_label)

        self._session_label = QLabel("")
        self._session_label.setObjectName("Muted")
        self._session_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        root.addWidget(self._session_label)

    # ------------------------------------------------------------------ #
    def show_placeholder(self, symbol: str = "") -> None:
        """Reset to an empty/loading state (used while the first quote loads)."""
        self._symbol = symbol
        self._last_price = None
        self._name_label.setText(symbol or "—")
        self._symbol_label.setText("Loading…" if symbol else "")
        self._price_roller.set_value(None)
        self._price_label.setStyleSheet("")
        self._price_label.setText("—")
        self._change_label.setText("")
        self._session_label.setText("")

    def update_quote(self, quote: Quote) -> None:
        self._symbol = quote.symbol
        name = quote.display_name
        self._name_label.setText(name)
        # Don't print the ticker twice when it is all we know.
        self._symbol_label.setText(quote.symbol if name != quote.symbol else "")

        # Flash the price green/red on a tick, then roll it to the new value.
        self._price_flash.set_base_style("", theme.color("text"))
        if self._last_price is not None and quote.price != self._last_price:
            self._price_flash.flash(
                theme.gain_color() if quote.price > self._last_price else theme.loss_color())
        self._last_price = quote.price
        self._price_roller.set_value(quote.price)

        color = theme.color_for(quote.change)
        arrow = "▲ " if quote.change > 0 else ("▼ " if quote.change < 0 else "")
        self._change_label.setText(
            f"{arrow}{fmt_signed_money(quote.change)} ({fmt_signed_pct(quote.change_pct)}) Today"
        )
        self._change_label.setStyleSheet(
            f"font-size: 14px; font-weight: 600; color: {color};")

        state = _market_state_text(quote.market_state)
        updated = f"Updated {fmt_time(quote.timestamp)}"
        sep = f"<span style='color:{theme.color('text_faint')}'>  ·  </span>"
        parts = [p for p in (state, updated) if p]
        self._session_label.setText(sep.join(parts))

    @property
    def symbol(self) -> str:
        return self._symbol


class DayStatsCard(QFrame):
    """Open / High / Low / Previous close / Volume for the active symbol."""

    _KEYS = ("Open", "High", "Low", "Prev Close", "Volume")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 11, 18, 11)
        root.setSpacing(7)

        self._title = QLabel("TODAY")
        self._title.setObjectName("SectionTitle")
        root.addWidget(self._title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(34)
        grid.setVerticalSpacing(3)
        self._values: dict[str, QLabel] = {}
        for col, key in enumerate(self._KEYS):
            title = QLabel(key.upper())
            title.setObjectName("Faint")
            title.setStyleSheet("font-size: 10px; letter-spacing: 0.6px;")
            value = QLabel("—")
            value.setStyleSheet("font-size: 14px; font-weight: 700;")
            grid.addWidget(title, 0, col)
            grid.addWidget(value, 1, col)
            self._values[key] = value
        grid.setColumnStretch(len(self._KEYS), 1)
        root.addLayout(grid)

    def clear(self) -> None:
        for value in self._values.values():
            value.setText("—")

    def update_quote(self, quote: Quote) -> None:
        self._title.setText(f"{quote.symbol} TODAY")
        self._values["Open"].setText(fmt_price(quote.day_open))
        self._values["High"].setText(fmt_price(quote.day_high))
        self._values["Low"].setText(fmt_price(quote.day_low))
        self._values["Prev Close"].setText(fmt_price(quote.previous_close))
        self._values["Volume"].setText(
            fmt_compact(quote.volume) if quote.volume else "—")


def _market_state_text(state: str) -> str:
    mapping = {
        "REGULAR": "● Market open",
        "PRE": "○ Pre-market",
        "PREPRE": "○ Pre-market",
        "POST": "○ After hours",
        "POSTPOST": "○ After hours",
        "CLOSED": "● Market closed",
    }
    return mapping.get((state or "").upper(), "")
