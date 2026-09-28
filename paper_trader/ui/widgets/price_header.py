"""The price hero: company name, live price and the change over the chart's range.

This is the piece that sets the tone of the page — the name and the price are
the two largest things on screen, with the change underneath and the session
state below that. As on the reference, the change line follows the chart: it
reads "Today" on the 1D chart and "Past week", "Year to date"… on the others,
and scrubbing the chart shows the price under the crosshair until the cursor
leaves. Day statistics live in a separate :class:`DayStatsCard` so the hero
stays uncluttered.

Both are pure views: they receive :class:`Quote` objects and render them, and
never fetch anything.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
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
from ..anim import ColorFlash, NumberRoller, RollingLabel
from ..format import (
    fmt_compact,
    fmt_price,
    fmt_signed_money,
    fmt_signed_pct,
    fmt_time,
)

# What the change line calls each chart range (the reference's wording).
RANGE_LABELS = {
    "1D": "Today",
    "1W": "Past week",
    "1M": "Past month",
    "3M": "Past 3 months",
    "YTD": "Year to date",
    "1Y": "Past year",
    "5Y": "Past 5 years",
    "ALL": "All time",
}


class PriceHeader(QWidget):
    """Displays the active symbol's name, price and change."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Clear")
        self._symbol = ""
        self._last_price: float | None = None
        self._quote: Quote | None = None
        self._range_label = "Today"
        self._reference: float | None = None   # what the change is measured from
        self._hovering = False
        self._build()
        self.show_placeholder()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- name (+ ticker) -------------------------------------------------- #
        ident = QHBoxLayout()
        ident.setSpacing(12)
        self._name_label = ElidedLabel("—")
        self._name_label.setObjectName("Ticker")
        theme.display_cut(self._name_label, 32)
        self._symbol_label = QLabel("")
        self._symbol_label.setObjectName("TickerChip")
        ident.addWidget(self._name_label)
        ident.addWidget(self._symbol_label, 0, Qt.AlignmentFlag.AlignBottom)
        ident.addStretch(1)
        root.addLayout(ident)

        # -- price ------------------------------------------------------------ #
        # A rolling label: live ticks count up to the new price, and scrubbing the
        # chart turns the digits over as the crosshair moves (see show_point).
        self._price_label = RollingLabel("—")
        self._price_label.setObjectName("BigPrice")
        self._price_label.setFont(theme.tabular(self._price_label.font()))
        theme.display_cut(self._price_label, 32)
        self._price_roller = NumberRoller(self._price_label, fmt_price, duration=420)
        self._price_flash = ColorFlash(self._price_label)
        root.addWidget(self._price_label)
        root.addSpacing(6)

        # -- change + session ------------------------------------------------- #
        self._change_label = QLabel("")
        self._change_label.setTextFormat(Qt.TextFormat.RichText)
        self._change_label.setStyleSheet("font-size: 14px; font-weight: 700;")
        root.addWidget(self._change_label)
        root.addSpacing(3)

        self._session_label = QLabel("")
        self._session_label.setTextFormat(Qt.TextFormat.RichText)
        self._session_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        root.addWidget(self._session_label)

    # ------------------------------------------------------------------ #
    def show_placeholder(self, symbol: str = "") -> None:
        """Reset to an empty/loading state (used while the first quote loads)."""
        self._symbol = symbol
        self._last_price = None
        self._quote = None
        self._reference = None
        self._hovering = False
        self._name_label.setText(symbol or "—")
        self._symbol_label.setText("Loading…" if symbol else "")
        self._price_roller.set_value(None)
        self._price_label.setStyleSheet("")
        self._price_label.setText("—")
        self._change_label.setText("")
        self._session_label.setText("")

    def update_quote(self, quote: Quote) -> None:
        self._symbol = quote.symbol
        self._quote = quote
        name = quote.display_name
        self._name_label.setText(name)
        # Don't print the ticker twice when it is all we know.
        self._symbol_label.setText(quote.symbol if name != quote.symbol else "")

        # Flash the price green/red on a tick, then roll it to the new value —
        # unless the chart is being scrubbed, when the hovered price owns it.
        self._price_flash.set_base_style("", theme.color("text"))
        if not self._hovering:
            if self._last_price is not None and quote.price != self._last_price:
                self._price_flash.flash(
                    theme.gain_color() if quote.price > self._last_price else theme.loss_color())
            self._price_roller.set_value(quote.price)
        self._last_price = quote.price
        if self._range_label == "Today":
            self._reference = quote.previous_close or None
        if not self._hovering:
            self._render_change(quote.price)
        self._render_session(quote)

    def set_range(self, label: str, reference: float | None) -> None:
        """Measure the change line over the chart's range (``reference`` = its start)."""
        self._range_label = label
        if reference:
            self._reference = reference
        elif label == "Today" and self._quote is not None:
            self._reference = self._quote.previous_close or None
        else:
            self._reference = None      # until the range's first price arrives
        if not self._hovering and self._last_price is not None:
            self._render_change(self._last_price)

    def show_point(self, price: float) -> None:
        """Show the price under the chart's crosshair (Robinhood-style scrub)."""
        self._hovering = True
        self._price_roller.set_value(price, animate=False, roll=True)
        self._render_change(price)

    def clear_point(self) -> None:
        """Cursor left the chart: back to the live price."""
        if not self._hovering:
            return
        self._hovering = False
        if self._last_price is not None:
            self._price_roller.set_value(self._last_price, animate=False, roll=True)
            self._render_change(self._last_price)

    @property
    def symbol(self) -> str:
        return self._symbol

    # ------------------------------------------------------------------ #
    def _render_change(self, price: float) -> None:
        ref = self._reference
        if not ref:
            self._change_label.setText("")
            return
        change = price - ref
        pct = change / ref * 100.0
        color = theme.color_for(change)
        self._change_label.setText(
            f"<span style='color:{color}'>{fmt_signed_money(change)} "
            f"({fmt_signed_pct(pct)})</span>"
            f"<span style='color:{theme.color('text')}'>&nbsp;{self._range_label}</span>")

    def _render_session(self, quote: Quote) -> None:
        state = (quote.market_state or "").upper()
        label, dot = _SESSION.get(state, ("", "text_faint"))
        faint = theme.color("text_faint")
        muted = theme.color("text_muted")
        parts = []
        if label:
            parts.append(f"<span style='color:{theme.color(dot)}'>●</span>"
                         f"<span style='color:{muted}'>&nbsp;&nbsp;{label}</span>")
        parts.append(f"<span style='color:{faint}'>Updated {fmt_time(quote.timestamp)}</span>")
        sep = f"<span style='color:{faint}'>&nbsp;&nbsp;·&nbsp;&nbsp;</span>"
        self._session_label.setText(sep.join(parts))


# Session name and the palette key for its dot: green while the regular market
# is open, the reference's lime for the extended sessions.
_SESSION = {
    "REGULAR": ("Market open", "green"),
    "PRE": ("Pre-market", "lime"),
    "PREPRE": ("Pre-market", "lime"),
    "POST": ("After hours", "lime"),
    "POSTPOST": ("After hours", "lime"),
    "CLOSED": ("Market closed", "text_faint"),
}


class ElidedLabel(QLabel):
    """A label that shortens itself with an ellipsis rather than widen the page.

    A long company name at hero size ("NVIDIA Corporation") would otherwise
    set a floor on the whole centre column's width.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._full = text or ""
        self.setToolTip(self._full)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 4, hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(80, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(0, self.width()))
        if shown != super().text():
            super().setText(shown)


class DayStatsCard(QFrame):
    """Open / High / Low / Previous close / Volume for the active symbol."""

    _KEYS = (("Open", "Open"), ("High", "High"), ("Low", "Low"),
             ("Prev Close", "Prev close"), ("Volume", "Volume"))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Box")
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 13)
        root.setSpacing(9)

        self._title = QLabel("Key statistics")
        self._title.setObjectName("H3")
        root.addWidget(self._title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(30)
        grid.setVerticalSpacing(3)
        self._values: dict[str, QLabel] = {}
        for col, (key, title_text) in enumerate(self._KEYS):
            title = QLabel(title_text)
            title.setObjectName("StatTitle")
            value = QLabel("—")
            value.setObjectName("StatValue")
            value.setFont(theme.tabular(value.font()))
            grid.addWidget(title, 0, col)
            grid.addWidget(value, 1, col)
            self._values[key] = value
        grid.setColumnStretch(len(self._KEYS), 1)
        root.addLayout(grid)

    def clear(self) -> None:
        for value in self._values.values():
            value.setText("—")

    def update_quote(self, quote: Quote) -> None:
        self._title.setText(f"Key statistics  ·  {quote.symbol}")
        self._values["Open"].setText(fmt_price(quote.day_open))
        self._values["High"].setText(fmt_price(quote.day_high))
        self._values["Low"].setText(fmt_price(quote.day_low))
        self._values["Prev Close"].setText(fmt_price(quote.previous_close))
        self._values["Volume"].setText(
            fmt_compact(quote.volume) if quote.volume else "—")
