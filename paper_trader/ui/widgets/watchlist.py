"""The watchlist rail (left column).

Rows in the style of Robinhood's lists: the symbol in bold with the company
name under it, a sparkline of the day in the stock's colour, and the price with
the day's change beneath it. Rows update in place — no rebuilds on quote ticks,
so nothing flickers and the selection survives.

Symbol *search* lives in the top nav bar; the owner validates a hit and calls
:meth:`add_symbol` here, which keeps every symbol in the list one the data
provider actually recognises.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ...data.models import Quote
from .. import icons, theme
from ..format import fmt_price, fmt_signed_pct

_ROW_HEIGHT = 58
_SPARK_W = 58
_SPARK_H = 26

# Item data roles.
_SYMBOL = Qt.ItemDataRole.UserRole
_NAME = Qt.ItemDataRole.UserRole + 1
_PRICE = Qt.ItemDataRole.UserRole + 2
_CHANGE = Qt.ItemDataRole.UserRole + 3
_PCT = Qt.ItemDataRole.UserRole + 4
_SPARK = Qt.ItemDataRole.UserRole + 5
_REF = Qt.ItemDataRole.UserRole + 6
_DATA_ROLES = (_NAME, _PRICE, _CHANGE, _PCT, _SPARK, _REF)


class WatchlistPanel(QWidget):
    symbolSelected = pyqtSignal(str)       # user clicked a symbol to view it
    watchlistChanged = pyqtSignal(object)  # list[str] — persisted by the owner
    addRequested = pyqtSignal()            # the footer row: take me to the search box

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Clear")
        self._symbols: list[str] = []
        self._items: dict[str, QListWidgetItem] = {}
        self._active: str = ""
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 4, 0)
        title = QLabel("Watchlist")
        title.setObjectName("SectionTitle")
        self._count = QLabel("")
        self._count.setObjectName("Faint")
        self._count.setStyleSheet("font-size: 12px; font-weight: 600;")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._count)
        root.addLayout(head)

        self._list = QListWidget()
        self._list.setObjectName("Watchlist")
        self._list.setItemDelegate(_RowDelegate(self._list))
        self._list.setUniformItemSizes(True)
        self._list.setMouseTracking(True)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list.setCursor(Qt.CursorShape.PointingHandCursor)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self._list, 1)

        # A footer row rather than a caption: where the eye lands after the last
        # symbol, and it takes you to the search box that adds one.
        rule = QFrame()
        rule.setObjectName("Divider")
        root.addWidget(rule)
        self._add = QPushButton("Add a symbol")
        self._add.setObjectName("AddRow")
        self._add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add.setIconSize(QSize(14, 14))
        self._add.setToolTip("Search for a symbol to add to the list")
        self._add.clicked.connect(lambda _=False: self.addRequested.emit())
        root.addWidget(self._add)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        self._add.setIcon(icons.icon("plus", theme.color("text_muted"), 14))

    # ------------------------------------------------------------------ #
    # Watchlist data
    # ------------------------------------------------------------------ #
    def set_watchlist(self, symbols: list[str]) -> None:
        """Replace the whole list (structure change), keeping known rows' data."""
        self._symbols = list(dict.fromkeys(s.upper() for s in symbols))
        # Copy the rows' data out first: clear() deletes the items themselves.
        kept = {sym: {role: item.data(role) for role in _DATA_ROLES}
                for sym, item in self._items.items()}
        self._list.clear()
        self._items = {}
        for sym in self._symbols:
            item = QListWidgetItem(sym)
            item.setData(_SYMBOL, sym)
            item.setSizeHint(QSize(0, _ROW_HEIGHT))
            for role, value in kept.get(sym, {}).items():
                item.setData(role, value)
            self._list.addItem(item)
            self._items[sym] = item
        self._count.setText(str(len(self._symbols)) if self._symbols else "")
        self._highlight_active()

    def update_quotes(self, quotes: dict[str, Quote]) -> None:
        """Update price/change in place for known symbols."""
        for sym, quote in quotes.items():
            item = self._items.get(sym)
            if item is None:
                continue
            name = quote.display_name
            item.setData(_NAME, name if name and name != sym else "")
            item.setData(_PRICE, float(quote.price))
            item.setData(_CHANGE, float(quote.change))
            item.setData(_PCT, float(quote.change_pct))
            item.setData(_REF, float(quote.previous_close or 0.0))

    def update_sparklines(self, series: dict[str, list[float]]) -> None:
        """Draw each symbol's day (a thinned list of closes) in its row."""
        for sym, closes in series.items():
            item = self._items.get(sym.upper())
            if item is not None and closes:
                item.setData(_SPARK, tuple(float(c) for c in closes))

    def set_active(self, symbol: str) -> None:
        self._active = symbol.upper()
        self._highlight_active()

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def contains(self, symbol: str) -> bool:
        return symbol.strip().upper() in self._items

    def add_symbol(self, symbol: str, *, select: bool = True) -> None:
        """Add a (already validated) symbol and optionally make it active."""
        symbol = symbol.strip().upper()
        if not symbol:
            return
        if symbol not in self._items:
            self._symbols.append(symbol)
            self.set_watchlist(self._symbols)
            self.watchlistChanged.emit(list(self._symbols))
        if select:
            self.set_active(symbol)
            self.symbolSelected.emit(symbol)

    def remove_symbol(self, symbol: str) -> None:
        symbol = symbol.strip().upper()
        if symbol in self._items:
            self._symbols.remove(symbol)
            self.set_watchlist(self._symbols)
            self.watchlistChanged.emit(list(self._symbols))

    # ------------------------------------------------------------------ #
    def _highlight_active(self) -> None:
        item = self._items.get(self._active)
        if item is not None:
            self._list.setCurrentItem(item)
        else:
            self._list.clearSelection()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        sym = item.data(_SYMBOL)
        if sym:
            self._active = sym
            self.symbolSelected.emit(sym)

    def _on_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        sym = item.data(_SYMBOL)
        menu = QMenu(self)
        menu.addAction(f"View {sym}", lambda: self.symbolSelected.emit(sym))
        menu.addSeparator()
        menu.addAction(f"Remove {sym}", lambda: self.remove_symbol(sym))
        menu.exec(self._list.viewport().mapToGlobal(pos))


class _RowDelegate(QStyledItemDelegate):
    """Paints one watchlist row: symbol/name, sparkline, price/change."""

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(option.rect.width(), _ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(0, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.color("selection" if selected else "hover")))
            painter.drawRect(QRectF(rect))

        inner = rect.adjusted(12, 0, -12, 0)
        symbol = index.data(_SYMBOL) or ""
        name = index.data(_NAME) or ""
        price = index.data(_PRICE)
        change = index.data(_CHANGE)
        pct = index.data(_PCT)
        spark = index.data(_SPARK)
        ref = index.data(_REF)
        trend = theme.color_for(change) if change is not None else theme.color("text_faint")

        base = QFont(option.font)
        bold = QFont(base)
        bold.setWeight(QFont.Weight.DemiBold)
        small = theme.resized(base, -2)
        theme.tabular(bold)
        mid_y = inner.center().y()

        # -- right: price over change --------------------------------------- #
        price_text = fmt_price(price) if price is not None else "—"
        pct_text = fmt_signed_pct(pct) if pct is not None else ""
        right_w = max(QFontMetrics(bold).horizontalAdvance(price_text),
                      QFontMetrics(small).horizontalAdvance(pct_text)) + 2
        right_x = inner.right() - right_w
        painter.setFont(bold)
        painter.setPen(QColor(theme.color("text")))
        painter.drawText(QRectF(right_x, inner.top(), right_w, mid_y - inner.top()),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, price_text)
        if pct_text:
            painter.setFont(small)
            painter.setPen(QColor(trend))
            painter.drawText(QRectF(right_x, mid_y + 2, right_w, inner.bottom() - mid_y - 2),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, pct_text)

        # -- middle: the day's sparkline (only when there is room for it) ---- #
        spark_right = right_x - 12
        spark_left = spark_right - _SPARK_W
        text_right = right_x - 10
        if spark and len(spark) > 1 and spark_left - inner.left() >= 64:
            _draw_sparkline(painter, QRectF(spark_left, mid_y - _SPARK_H / 2, _SPARK_W, _SPARK_H),
                            spark, ref, trend)
            text_right = spark_left - 10

        # -- left: symbol over name ------------------------------------------ #
        text_w = max(10, text_right - inner.left())
        painter.setFont(bold)
        painter.setPen(QColor(theme.color("text")))
        painter.drawText(QRectF(inner.left(), inner.top(), text_w, mid_y - inner.top()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                         QFontMetrics(bold).elidedText(symbol, Qt.TextElideMode.ElideRight,
                                                       int(text_w)))
        if name:
            painter.setFont(small)
            painter.setPen(QColor(theme.color("text_muted")))
            painter.drawText(QRectF(inner.left(), mid_y + 2, text_w, inner.bottom() - mid_y - 2),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             QFontMetrics(small).elidedText(name, Qt.TextElideMode.ElideRight,
                                                            int(text_w)))
        painter.restore()


def _draw_sparkline(painter: QPainter, box: QRectF, closes, ref, color: str) -> None:
    """A day in miniature: the closes as a line, the previous close dotted."""
    lo, hi = min(closes), max(closes)
    if ref:
        lo, hi = min(lo, ref), max(hi, ref)
    span = (hi - lo) or 1.0
    n = len(closes)

    def y_of(v: float) -> float:
        return box.bottom() - (v - lo) / span * box.height()

    if ref:
        pen = QPen(QColor(theme.color("baseline")), 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setDashPattern([0.01, 3.0])
        painter.setPen(pen)
        y = y_of(ref)
        painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))

    path = QPainterPath()
    for i, v in enumerate(closes):
        pt = QPointF(box.left() + box.width() * i / (n - 1), y_of(v))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    pen = QPen(QColor(color), 1.5)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
