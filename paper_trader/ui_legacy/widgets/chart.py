"""Interactive price chart (line/area + candlestick) — the pre-rework version.

Range buttons and the Line/Candles toggle sit *above* the plot, the line is drawn
in the trend colour with a translucent fill underneath, and the view frames the
whole series: zooming pans the time axis only, with the price axis left where it
was. (The current chart refits price to the visible window instead; this one is
kept as it was so ``--old`` really is the old behaviour.)

pyqtgraph is used for its speed: updates go through ``setData`` on persistent
plot items, so refreshes are allocation-light and flicker-free. The candlestick
renderer is a small custom ``GraphicsObject`` that paints once into a ``QPicture``
and blits it.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import QPainter, QPicture
from PyQt6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import CHART_RANGES
from ...data.models import Candle
from ...ui import anim, theme
from ...ui.format import fmt_price
from ...ui.widgets.chart import TimeAxis, close_gaps

pg.setConfigOptions(antialias=True)


# --------------------------------------------------------------------------- #
# Candlestick graphics item
# --------------------------------------------------------------------------- #
class CandlestickItem(pg.GraphicsObject):
    """Draws OHLC candles from a list of :class:`Candle`."""

    def __init__(self) -> None:
        super().__init__()
        self._picture = QPicture()
        self._rect = QRectF()

    def set_data(self, candles: list[Candle], up_color: str, down_color: str,
                 xs=None) -> None:
        self._picture = QPicture()
        if not candles:
            self._rect = QRectF()
            self.informViewBoundsChanged()
            self.update()
            return

        if xs is None:
            xs = [c.epoch for c in candles]
        # Candle body width = 70% of the median time step between bars.
        if len(xs) >= 2:
            step = float(np.median(np.diff(xs)))
        else:
            step = 60.0
        half = step * 0.35

        painter = QPainter(self._picture)
        up_pen = pg.mkPen(up_color, width=0)
        down_pen = pg.mkPen(down_color, width=0)
        up_brush = pg.mkBrush(up_color)
        down_brush = pg.mkBrush(down_color)

        lo_min = min(c.low for c in candles)
        hi_max = max(c.high for c in candles)
        for c, x in zip(candles, xs):
            x = float(x)
            rising = c.close >= c.open
            painter.setPen(up_pen if rising else down_pen)
            painter.setBrush(up_brush if rising else down_brush)
            painter.drawLine(QPointF(x, c.low), QPointF(x, c.high))  # wick
            top = max(c.open, c.close)
            bottom = min(c.open, c.close)
            if top == bottom:
                top += (hi_max - lo_min) * 0.0005 or 0.0001
            painter.drawRect(QRectF(x - half, bottom, half * 2, top - bottom))
        painter.end()

        self._rect = QRectF(xs[0] - half, lo_min, (xs[-1] - xs[0]) + step, hi_max - lo_min)
        self.informViewBoundsChanged()
        self.update()

    def clear(self) -> None:
        self._picture = QPicture()
        self._rect = QRectF()
        self.update()

    def paint(self, painter, *args) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QRectF:
        return self._rect if not self._rect.isNull() else QRectF(self._picture.boundingRect())


# --------------------------------------------------------------------------- #
# Chart widget
# --------------------------------------------------------------------------- #
class ChartWidget(QWidget):
    """Price chart with range selector, line/candle toggle and a crosshair."""

    rangeChanged = pyqtSignal(str)  # emitted when the user picks a new range

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._candles: list[Candle] = []
        self._symbol = ""
        self._range = "1D"
        self._mode = "line"          # "line" | "candles"
        self._prev_close: float | None = None
        self._last_key: tuple | None = None
        # Line "draw-in" animation state.
        self._drawing = False
        self._draw_params: tuple | None = None
        self._draw_anim = QVariantAnimation(self)
        self._draw_anim.setDuration(620)
        self._draw_anim.setStartValue(0.0)
        self._draw_anim.setEndValue(1.0)
        self._draw_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._draw_anim.valueChanged.connect(self._on_draw_step)
        self._draw_anim.finished.connect(self._on_draw_done)
        self._build()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # -- top control bar ------------------------------------------------ #
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self._range_group = QButtonGroup(self)
        self._range_group.setExclusive(True)
        for key in CHART_RANGES:
            btn = QPushButton(key)
            btn.setObjectName("RangeTab")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._on_range_clicked(k))
            self._range_group.addButton(btn)
            bar.addWidget(btn)
            if key == self._range:
                btn.setChecked(True)

        bar.addStretch(1)

        self._hover_label = QLabel("")
        self._hover_label.setObjectName("Muted")
        self._hover_label.setStyleSheet("font-size: 12px;")
        bar.addWidget(self._hover_label)
        bar.addSpacing(10)

        self._type_group = QButtonGroup(self)
        self._type_group.setExclusive(True)
        for label, mode in (("Line", "line"), ("Candles", "candles")):
            btn = QPushButton(label)
            btn.setObjectName("Segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, m=mode: self._on_mode_clicked(m))
            self._type_group.addButton(btn)
            bar.addWidget(btn)
            if mode == self._mode:
                btn.setChecked(True)
        root.addLayout(bar)

        # -- plot ----------------------------------------------------------- #
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)

        # Multi-day ranges plot with the market's closed hours taken out.
        self._time_axis = TimeAxis()
        self._plot_xs = np.empty(0)
        self._plot = pg.PlotWidget(axisItems={"bottom": self._time_axis})
        self._plot.setMenuEnabled(False)
        self._plot.setMouseEnabled(x=True, y=False)
        self._plot.hideButtons()
        self._plot.setClipToView(True)
        vb = self._plot.getViewBox()
        vb.setDefaultPadding(0.02)

        self._line_item = pg.PlotDataItem()
        self._candle_item = CandlestickItem()
        self._baseline = pg.InfiniteLine(angle=0, movable=False)
        self._vline = pg.InfiniteLine(angle=90, movable=False)
        self._hline = pg.InfiniteLine(angle=0, movable=False)
        for item in (self._line_item, self._candle_item, self._baseline,
                     self._vline, self._hline):
            self._plot.addItem(item)
        self._vline.hide()
        self._hline.hide()
        self._baseline.hide()

        self._plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

        self._empty_label = QLabel("No chart data available")
        self._empty_label.setObjectName("Faint")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()

        grid.addWidget(self._plot, 0, 0)
        grid.addWidget(self._empty_label, 0, 0, Qt.AlignmentFlag.AlignCenter)
        root.addLayout(grid, 1)

        self.apply_theme()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def current_range(self) -> str:
        return self._range

    def current_mode(self) -> str:
        return self._mode

    def set_range(self, range_key: str) -> None:
        """Programmatically select a range (without emitting rangeChanged)."""
        if range_key not in CHART_RANGES:
            return
        self._range = range_key
        for btn in self._range_group.buttons():
            btn.setChecked(btn.text() == range_key)

    def set_symbol(self, symbol: str) -> None:
        if symbol != self._symbol:
            self._symbol = symbol
            self._candles = []
            self._prev_close = None
            self._last_key = None
            self._hover_label.setText("")
            self._render()

    def update_reference(self, previous_close: float | None) -> None:
        """Provide the previous close so a 1D baseline can be drawn."""
        self._prev_close = previous_close
        if self._mode == "line":
            self._render()

    def set_candles(self, symbol: str, range_key: str, candles: list[Candle]) -> None:
        """Receive a fresh candle series (from the feed) and redraw."""
        if symbol != self._symbol or range_key != self._range:
            return  # stale payload for a symbol/range the user has moved past
        self._candles = candles
        self._render()

    def apply_theme(self) -> None:
        c = theme.chart_colors()
        self._plot.setBackground(c["background"])
        for axis in ("bottom", "left"):
            ax = self._plot.getAxis(axis)
            ax.setPen(pg.mkPen(c["axis"]))
            ax.setTextPen(pg.mkPen(c["text"]))
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._baseline.setPen(pg.mkPen(c["text"], width=1, style=Qt.PenStyle.DashLine))
        cross = pg.mkPen(c["crosshair"], width=1, style=Qt.PenStyle.DashLine)
        self._vline.setPen(cross)
        self._hline.setPen(cross)
        self._render()

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _render(self) -> None:
        has_data = bool(self._candles)
        self._empty_label.setVisible(not has_data)
        self._plot.setVisible(True)
        colors = theme.chart_colors()

        if not has_data:
            self._draw_anim.stop()
            self._drawing = False
            self._line_item.clear()
            self._candle_item.clear()
            self._baseline.hide()
            return

        epochs = np.array([c.epoch for c in self._candles], dtype=float)
        if self._range == "1D":
            xs = epochs
            self._time_axis.set_mapping(None)
        else:
            xs = close_gaps(epochs)
            self._time_axis.set_mapping(xs, epochs)
        self._plot_xs = xs
        closes = np.array([c.close for c in self._candles], dtype=float)
        rising = closes[-1] >= closes[0]
        trend = colors["up"] if rising else colors["down"]

        key = (self._symbol, self._range, self._mode, len(self._candles))
        is_new = key[:2] != (self._last_key[:2] if self._last_key else None)

        if self._mode == "candles":
            self._draw_anim.stop()
            self._drawing = False
            self._line_item.hide()
            self._candle_item.show()
            self._candle_item.set_data(self._candles, colors["up"], colors["down"], xs)
            y_lo = float(min(c.low for c in self._candles))
            y_hi = float(max(c.high for c in self._candles))
        else:
            self._candle_item.hide()
            self._line_item.show()
            fill = pg.mkColor(trend)
            fill.setAlpha(45)
            pen = pg.mkPen(trend, width=2)
            fill_level = float(closes.min())
            self._draw_params = (xs, closes, pen, fill_level, fill)
            if is_new and anim.ENABLED and len(xs) > 3:
                self._start_draw()   # Robinhood-style left-to-right reveal
            elif not self._drawing:
                self._set_line(xs, closes, pen, fill_level, fill)
            y_lo, y_hi = float(closes.min()), float(closes.max())

        # 1D baseline at the previous close.
        if self._range == "1D" and self._prev_close:
            self._baseline.setValue(self._prev_close)
            self._baseline.show()
            y_lo = min(y_lo, self._prev_close)
            y_hi = max(y_hi, self._prev_close)
        else:
            self._baseline.hide()

        # Frame the data only when the dataset changes, so a manual zoom/pan
        # survives the periodic refresh.
        if is_new:
            pad = (y_hi - y_lo) * 0.08 or 1.0
            self._plot.setXRange(xs[0], xs[-1], padding=0.02)
            self._plot.setYRange(y_lo - pad, y_hi + pad, padding=0)
        self._last_key = key

    # ------------------------------------------------------------------ #
    # Line draw-in animation
    # ------------------------------------------------------------------ #
    def _set_line(self, xs, closes, pen, fill_level, brush, k: int | None = None) -> None:
        """Draw the line using the first ``k`` points (all of them if ``k`` is None)."""
        if k is None or k >= len(xs):
            self._line_item.setData(xs, closes, pen=pen, fillLevel=fill_level, brush=brush)
        else:
            k = max(2, k)
            self._line_item.setData(xs[:k], closes[:k], pen=pen,
                                    fillLevel=fill_level, brush=brush)

    def _start_draw(self) -> None:
        self._draw_anim.stop()
        self._drawing = True
        xs, closes, pen, fill_level, brush = self._draw_params
        self._set_line(xs, closes, pen, fill_level, brush, k=2)  # seed
        self._draw_anim.start()

    def _on_draw_step(self, frac) -> None:
        if not self._drawing or self._draw_params is None:
            return
        xs, closes, pen, fill_level, brush = self._draw_params
        self._set_line(xs, closes, pen, fill_level, brush, k=int(float(frac) * len(xs)))

    def _on_draw_done(self) -> None:
        self._drawing = False
        if self._draw_params is not None:
            xs, closes, pen, fill_level, brush = self._draw_params
            self._set_line(xs, closes, pen, fill_level, brush)

    # ------------------------------------------------------------------ #
    # Interaction
    # ------------------------------------------------------------------ #
    def _on_range_clicked(self, range_key: str) -> None:
        self._range = range_key
        self._candles = []
        self._last_key = None
        self.rangeChanged.emit(range_key)

    def _on_mode_clicked(self, mode: str) -> None:
        self._mode = mode
        self._last_key = None
        self._render()

    def _on_mouse_moved(self, pos) -> None:
        if not self._candles:
            return
        vb = self._plot.getViewBox()
        if not self._plot.sceneBoundingRect().contains(pos):
            self._vline.hide()
            self._hline.hide()
            return
        point = vb.mapSceneToView(pos)
        x = point.x()
        xs = self._plot_xs
        if len(xs) != len(self._candles):
            return
        idx = int(np.argmin(np.abs(xs - x)))
        candle = self._candles[idx]
        self._vline.setValue(float(xs[idx]))
        self._hline.setValue(candle.close)
        self._vline.show()
        self._hline.show()
        color = theme.color_for(candle.close - candle.open)
        when = candle.time.astimezone().strftime("%b %d %H:%M")
        self._hover_label.setText(
            f"<span style='color:{theme.muted_color()}'>{when}</span>  "
            f"O {fmt_price(candle.open)}  H {fmt_price(candle.high)}  "
            f"L {fmt_price(candle.low)}  "
            f"<span style='color:{color}'>C {fmt_price(candle.close)}</span>"
        )
