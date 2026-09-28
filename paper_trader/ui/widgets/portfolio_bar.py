"""Account strip: portfolio name, total value, today's change and stat tiles.

Sits directly under the nav bar as a full-width band with a hairline rule — the
account context for everything below it. A pure view, updated from a
:class:`PortfolioSnapshot` each time prices or holdings change.

The change line follows the reference's hero: only the figures take the
gain/loss colour; the words ("Today", "All time") stay in the text colour.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...core.portfolio import PortfolioSnapshot
from .. import theme
from ..anim import NumberRoller
from ..format import fmt_money, fmt_signed_money, fmt_signed_pct


class _Tile(QWidget):
    """A small titled value in the strip's stat row.

    With a ``formatter`` the value *rolls* to each new figure; signed values
    take the gain/loss colour underneath the roll.
    """

    def __init__(self, title: str, formatter: Callable[[float], str] | None = None) -> None:
        super().__init__()
        self.setObjectName("Clear")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(3)
        self._title = QLabel(title)
        self._title.setObjectName("StatTitle")
        self._value = QLabel("—")
        self._value.setObjectName("StatValue")
        self._value.setFont(theme.tabular(self._value.font()))
        box.addWidget(self._title)
        box.addWidget(self._value)
        self._roller = NumberRoller(self._value, formatter) if formatter else None

    def set_number(self, value: float, color: str | None = None) -> None:
        self._value.setStyleSheet(f"color: {color};" if color else "")
        if self._roller is not None:
            self._roller.set_value(value)


class PortfolioBar(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Strip")
        self._build()

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 12, 24, 14)
        root.setSpacing(0)

        left = QVBoxLayout()
        left.setSpacing(2)
        self._name = QLabel("My Portfolio")
        self._name.setObjectName("Kicker")
        value_row = QHBoxLayout()
        value_row.setSpacing(14)
        self._total = QLabel("$0.00")
        self._total.setObjectName("H1")
        self._total.setFont(theme.tabular(self._total.font()))
        self._total_roller = NumberRoller(self._total, fmt_money, duration=560)
        self._subline = QLabel("")
        self._subline.setTextFormat(Qt.TextFormat.RichText)
        self._subline.setStyleSheet("font-size: 13px; font-weight: 600;")
        value_row.addWidget(self._total)
        value_row.addWidget(self._subline, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch(1)
        left.addWidget(self._name)
        left.addLayout(value_row)
        root.addLayout(left, 1)

        self._tiles = {
            "Buying Power": _Tile("Buying power", fmt_money),
            "Market Value": _Tile("Market value", fmt_money),
            "Invested": _Tile("Invested", fmt_money),
            "Unrealized P/L": _Tile("Unrealized P/L", fmt_signed_money),
            "Realized P/L": _Tile("Realized P/L", fmt_signed_money),
        }
        for i, tile in enumerate(self._tiles.values()):
            if i:
                root.addSpacing(34)
            root.addWidget(tile, 0, Qt.AlignmentFlag.AlignVCenter)

    # ------------------------------------------------------------------ #
    def update_snapshot(self, snap: PortfolioSnapshot, name: str) -> None:
        self._name.setText(name.upper())
        self._total_roller.set_value(snap.total_value)

        text = theme.color("text")
        parts = []
        if snap.day_change is not None:
            c = theme.color_for(snap.day_change)
            arrow = "▲ " if snap.day_change > 0 else ("▼ " if snap.day_change < 0 else "")
            parts.append(
                f"<span style='color:{c}'>{arrow}{fmt_signed_money(snap.day_change)} "
                f"({fmt_signed_pct(snap.day_change_pct)})</span>"
                f"<span style='color:{text}'>&nbsp;Today</span>")
        c_all = theme.color_for(snap.total_pl)
        parts.append(
            f"<span style='color:{c_all}'>{fmt_signed_money(snap.total_pl)} "
            f"({fmt_signed_pct(snap.total_pl_pct)})</span>"
            f"<span style='color:{text}'>&nbsp;All time</span>")
        sep = f"<span style='color:{theme.color('text_faint')}'>&nbsp;&nbsp;·&nbsp;&nbsp;</span>"
        self._subline.setText(sep.join(parts))

        self._tiles["Buying Power"].set_number(snap.buying_power)
        self._tiles["Market Value"].set_number(snap.holdings_value)
        self._tiles["Invested"].set_number(snap.invested)
        self._tiles["Unrealized P/L"].set_number(
            snap.unrealized_pl, theme.color_for(snap.unrealized_pl))
        self._tiles["Realized P/L"].set_number(
            snap.realized_pl, theme.color_for(snap.realized_pl))
