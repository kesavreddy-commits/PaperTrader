"""Top header bar: account name, total value, today's change and stat tiles.

The pre-rework card treatment: a rounded panel with the account on the left and
divider-separated tiles on the right. A pure view updated from a
:class:`PortfolioSnapshot` each time prices or holdings change.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...core.portfolio import PortfolioSnapshot
from ...ui import theme
from ...ui.anim import NumberRoller
from ...ui.format import fmt_money, fmt_signed_money, fmt_signed_pct

_TILE_STYLE = "font-size: 15px; font-weight: 700;"


class _Tile(QWidget):
    """A small titled value used in the header's stat strip."""

    def __init__(self, title: str, formatter: Callable[[float], str] | None = None) -> None:
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 2, 14, 2)
        box.setSpacing(1)
        self._title = QLabel(title.upper())
        self._title.setObjectName("Faint")
        self._title.setStyleSheet("font-size: 10px; letter-spacing: 0.5px;")
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
        self.setObjectName("HeaderBar")
        self._build()

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(1)
        self._name = QLabel("My Portfolio")
        self._name.setObjectName("Muted")
        self._name.setStyleSheet("font-size: 12px; font-weight: 600;")
        self._total = QLabel("$0.00")
        self._total.setObjectName("H1")
        self._total_roller = NumberRoller(self._total, fmt_money, duration=560)
        self._subline = QLabel("")
        self._subline.setStyleSheet("font-size: 13px; font-weight: 600;")
        left.addWidget(self._name)
        left.addWidget(self._total)
        left.addWidget(self._subline)
        root.addLayout(left)

        root.addStretch(1)

        self._tiles = {
            "Buying Power": _Tile("Buying Power", fmt_money),
            "Market Value": _Tile("Market Value", fmt_money),
            "Invested": _Tile("Invested", fmt_money),
            "Unrealized P/L": _Tile("Unrealized P/L", fmt_signed_money),
            "Realized P/L": _Tile("Realized P/L", fmt_signed_money),
        }
        for i, tile in enumerate(self._tiles.values()):
            if i:
                div = QFrame(); div.setFrameShape(QFrame.Shape.VLine)
                div.setStyleSheet(f"color: {theme.color('border')};")
                root.addWidget(div)
            root.addWidget(tile, 0, Qt.AlignmentFlag.AlignVCenter)

    # ------------------------------------------------------------------ #
    def update_snapshot(self, snap: PortfolioSnapshot, name: str) -> None:
        self._name.setText(name)
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
        sep = f"<span style='color:{theme.muted_color()}'>  ·  </span>"
        self._subline.setText(sep.join(parts))

        self._tiles["Buying Power"].set_number(snap.buying_power)
        self._tiles["Market Value"].set_number(snap.holdings_value)
        self._tiles["Invested"].set_number(snap.invested)
        self._tiles["Unrealized P/L"].set_number(
            snap.unrealized_pl, theme.color_for(snap.unrealized_pl))
        self._tiles["Realized P/L"].set_number(
            snap.realized_pl, theme.color_for(snap.realized_pl))
