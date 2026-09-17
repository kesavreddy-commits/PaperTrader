"""Account strip: portfolio name, total value, today's change and stat tiles.

Sits directly under the nav bar as a full-width band with a hairline rule — the
account context for everything below it. A pure view, updated from a
:class:`PortfolioSnapshot` each time prices or holdings change.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...core.portfolio import PortfolioSnapshot
from .. import theme
from ..anim import NumberRoller
from ..format import fmt_money, fmt_signed_money, fmt_signed_pct

_TILE_STYLE = "font-size: 14px; font-weight: 700;"


class _Tile(QWidget):
    """A small titled value used in the strip's stat row.

    When given a ``formatter`` the numeric value *rolls* to each new figure; the
    colour (for signed P/L) is applied to the label's style underneath the roll.
    """

    def __init__(self, title: str, formatter: Callable[[float], str] | None = None) -> None:
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 0, 16, 0)
        box.setSpacing(2)
        self._title = QLabel(title.upper())
        self._title.setObjectName("Faint")
        self._title.setStyleSheet("font-size: 10px; letter-spacing: 0.6px;")
        self._value = QLabel("—")
        self._value.setStyleSheet(_TILE_STYLE)
        box.addWidget(self._title)
        box.addWidget(self._value)
        self._roller = NumberRoller(self._value, formatter) if formatter else None

    def set_number(self, value: float, color: str | None = None) -> None:
        self._value.setStyleSheet(_TILE_STYLE + (f" color: {color};" if color else ""))
        if self._roller is not None:
            self._roller.set_value(value)


class PortfolioBar(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Strip")
        self._build()

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(22, 12, 22, 12)
        root.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(2)
        self._name = QLabel("My Portfolio")
        self._name.setObjectName("Faint")
        self._name.setStyleSheet("font-size: 10px; font-weight: 700; letter-spacing: 0.6px;")
        value_row = QHBoxLayout()
        value_row.setSpacing(12)
        self._total = QLabel("$0.00")
        self._total.setObjectName("H1")
        self._total_roller = NumberRoller(self._total, fmt_money, duration=560)
        self._subline = QLabel("")
        self._subline.setStyleSheet("font-size: 13px; font-weight: 600;")
        value_row.addWidget(self._total)
        value_row.addWidget(self._subline, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch(1)
        left.addWidget(self._name)
        left.addLayout(value_row)
        root.addLayout(left)

        root.addStretch(1)

        self._tiles = {
            "Buying Power": _Tile("Buying Power", fmt_money),
            "Market Value": _Tile("Market Value", fmt_money),
            "Invested": _Tile("Invested", fmt_money),
            "Unrealized P/L": _Tile("Unrealized P/L", fmt_signed_money),
            "Realized P/L": _Tile("Realized P/L", fmt_signed_money),
        }
        for tile in self._tiles.values():
            root.addWidget(tile, 0, Qt.AlignmentFlag.AlignVCenter)

    # ------------------------------------------------------------------ #
    def update_snapshot(self, snap: PortfolioSnapshot, name: str) -> None:
        self._name.setText(name.upper())
        self._total_roller.set_value(snap.total_value)

        parts = []
        if snap.day_change is not None:
            arrow = "▲" if snap.day_change > 0 else ("▼" if snap.day_change < 0 else "•")
            c = theme.color_for(snap.day_change)
            parts.append(
                f"<span style='color:{c}'>{arrow} {fmt_signed_money(snap.day_change)} "
                f"({fmt_signed_pct(snap.day_change_pct)}) today</span>"
            )
        c_all = theme.color_for(snap.total_pl)
        parts.append(
            f"<span style='color:{c_all}'>{fmt_signed_money(snap.total_pl)} "
            f"({fmt_signed_pct(snap.total_pl_pct)}) all-time</span>"
        )
        sep = f"<span style='color:{theme.color('text_faint')}'>  ·  </span>"
        self._subline.setText(sep.join(parts))

        self._tiles["Buying Power"].set_number(snap.buying_power)
        self._tiles["Market Value"].set_number(snap.holdings_value)
        self._tiles["Invested"].set_number(snap.invested)
        self._tiles["Unrealized P/L"].set_number(
            snap.unrealized_pl, theme.color_for(snap.unrealized_pl))
        self._tiles["Realized P/L"].set_number(
            snap.realized_pl, theme.color_for(snap.realized_pl))
