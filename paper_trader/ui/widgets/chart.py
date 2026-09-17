"""Interactive price chart (line/area + candlestick) built on pyqtgraph.

The line view is deliberately chrome-free — no axes, no grid, just the price
line, a dotted previous-close reference and a crosshair — which is what makes a
Robinhood-style page read as a *price*, not a plot. The candlestick view is the
analytical counterpart: it fills exactly the same (large) area and turns the axes
back on so the bars can be read against a price scale.

Zoom behaviour is shared by both views. The wheel zooms the *time* axis and the
price axis then refits itself to whatever is on screen, so zooming in genuinely
magnifies the candles instead of squashing them into a flat band. The price axis
is not directly draggable for the same reason: it is always a consequence of the
visible time window.

pyqtgraph is used for its speed: updates go through ``setData`` on persistent
plot items, so refreshes are allocation-light and flicker-free. The candlestick
renderer is a small custom ``GraphicsObject`` that paints once into a ``QPicture``
and blits it — the standard high-performance pyqtgraph pattern.

The widget is a pure view: it holds the candle series it was given and redraws on
range/type changes, but it performs no network I/O. Range-button clicks are
surfaced via the :attr:`rangeChanged` signal for the owner to act on.
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
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...config import CHART_RANGES
from ...data.models import Candle
from .. import anim, theme
from ..format import fmt_compact, fmt_price

pg.setConfigOptions(antialias=True)

# Ranges whose bars are intraday — used to pick the hover-label time format.
_INTRADAY_RANGES = {"1D", "1W"}


# --------------------------------------------------------------------------- #
# Candlestick graphics item
# --------------------------------------------------------------------------- #
class CandlestickItem(pg.GraphicsObject):
    """Draws OHLC candles from a list of :class:`Candle`.

    Bodies are sized in *data* coordinates (a fraction of the bar interval), so
    they widen as the view zooms in. Wicks and outlines use cosmetic pens so they
    stay one device pixel wide at any zoom instead of being scaled to nothing by
    the wildly different x (seconds) and y (dollars) scales.
    """

    def __init__(self) -> None:
        super().__init__()
        self._picture = QPicture()
        self._rect = QRectF()

    def set_data(
        self, candles: list[Candle], up_color: str, down_color: str
    ) -> None:
        self._picture = QPicture()
        if not candles:
            self._rect = QRectF()
            self.informViewBoundsChanged()
            self.update()
            return

        xs = [c.epoch for c in candles]
        # Candle body width = 70% of the median time step between bars.
        step = float(np.median(np.diff(xs))) if len(xs) >= 2 else 60.0
        if not np.isfinite(step) or step <= 0:
            step = 60.0
        half = step * 0.35

        painter = QPainter(self._picture)
        # width=0 -> cosmetic: always exactly one device pixel, whatever the
        # view transform. A width of 1 here would mean "1 second" on the x axis.
        up_pen = pg.mkPen(up_color, width=0)
        down_pen = pg.mkPen(down_color, width=0)
        up_brush = pg.mkBrush(up_color)
        down_brush = pg.mkBrush(down_color)

        lo_min = min(c.low for c in candles)
        hi_max = max(c.high for c in candles)
        # A doji (open == close) still needs a visible body: give it a hairline
        # proportional to the series' own range.
        doji = (hi_max - lo_min) * 0.0006 or 0.0001
        for c in candles:
            rising = c.close >= c.open
            painter.setPen(up_pen if rising else down_pen)
            painter.setBrush(up_brush if rising else down_brush)
            x = c.epoch
            painter.drawLine(QPointF(x, c.low), QPointF(x, c.high))  # wick
            top = max(c.open, c.close)
            bottom = min(c.open, c.close)
            if top == bottom:
                top += doji
            painter.drawRect(QRectF(x - half, bottom, half * 2, top - bottom))
        painter.end()

        self._rect = QRectF(xs[0] - half, lo_min, (xs[-1] - xs[0]) + step, hi_max - lo_min)
        self.informViewBoundsChanged()
        self.update()

    def clear(self) -> None:
        self._picture = QPicture()
        self._rect = QRectF()
        self.informViewBoundsChanged()
        self.update()

    def paint(self, painter, *args) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QRectF:
        return self._rect if not self._rect.isNull() else QRectF(self._picture.boundingRect())


# --------------------------------------------------------------------------- #
# View box: zoom time and price together, in one update
# --------------------------------------------------------------------------- #
class PriceViewBox(pg.ViewBox):
    """A ViewBox whose wheel zoom sets both axes in a single range change.

    Letting the default wheel handler move x and then refitting y from the
    range-changed signal costs two full re-renders per notch *and* shows the
    price axis catching up a frame late — which is what a continuous zoom looks
    like when it stutters. Computing the matching price window up front and
    applying both at once makes each notch a single, atomic redraw.
    """

    def __init__(self) -> None:
        super().__init__()
        self.fit_y = None  # set by the chart: (x0, x1) -> (y0, y1) | None

    def wheelEvent(self, ev, axis=None) -> None:  # noqa: N802 (Qt naming)
        if self.fit_y is None or axis is not None:
            super().wheelEvent(ev, axis)
            return
        delta = ev.delta() if hasattr(ev, "delta") else ev.angleDelta().y()
        if not delta:
            ev.accept()
            return
        scale = 1.02 ** (delta * self.state["wheelScaleFactor"])
        centre = self.mapSceneToView(ev.scenePos()).x()
        (x0, x1), _ = self.viewRange()
        nx0 = centre + (x0 - centre) * scale
        nx1 = centre + (x1 - centre) * scale
        nx0, nx1 = self._clamp_x(nx0, nx1)
        window = self.fit_y(nx0, nx1)
        if window is None:
            super().wheelEvent(ev, axis)
            return
        self.setRange(xRange=(nx0, nx1), yRange=window, padding=0)
        ev.accept()

    def _clamp_x(self, x0: float, x1: float) -> tuple[float, float]:
        limits = self.state.get("limits", {}).get("xLimits", [None, None])
        lo, hi = limits[0], limits[1]
        span = x1 - x0
        if lo is not None and x0 < lo:
            x0, x1 = lo, lo + span
        if hi is not None and x1 > hi:
            x1, x0 = hi, hi - span
        if lo is not None and x0 < lo:
            x0 = lo
        return x0, x1


# --------------------------------------------------------------------------- #
# Chart widget
# --------------------------------------------------------------------------- #
class ChartWidget(QWidget):
    """Price chart with range selector, line/candle toggle and a crosshair."""

    rangeChanged = pyqtSignal(str)  # emitted when the user picks a new range

    # A floor, not a target: the chart takes all the height the splitter gives
    # it, but may shrink to this on a small window rather than squeezing its
    # neighbours off the screen.
    MIN_PLOT_HEIGHT = 200

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._candles: list[Candle] = []
        self._symbol = ""
        self._range = "1D"
        self._mode = "line"          # "line" | "candles"
        self._prev_close: float | None = None
        self._last_key: tuple | None = None
        self._hover_index: int | None = None
        self._suppress_autoscale = False
        self._reset_visible = False
        # The series is mirrored into numpy arrays: the zoom handler runs on
        # every wheel step and must not walk a Python list of candles each time.
        self._xs = np.empty(0)
        self._closes = np.empty(0)
        self._lows = np.empty(0)
        self._highs = np.empty(0)
        self._last_y: tuple[float, float] | None = None
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
        root.setSpacing(0)

        # -- plot ----------------------------------------------------------- #
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)

        self._view = PriceViewBox()
        self._view.fit_y = self._fit_price_window
        self._plot = pg.PlotWidget(viewBox=self._view,
                                   axisItems={"bottom": pg.DateAxisItem()})
        self._plot.setMenuEnabled(False)
        # The wheel zooms time; price refits itself (see _autoscale_y), so the
        # y axis is never dragged directly.
        self._plot.setMouseEnabled(x=True, y=False)
        self._plot.hideButtons()
        self._plot.setClipToView(True)
        self._plot.setMinimumHeight(self.MIN_PLOT_HEIGHT)
        self._plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vb = self._plot.getViewBox()
        vb.setDefaultPadding(0.0)
        vb.sigXRangeChanged.connect(self._on_x_range_changed)

        # Base line (dim) + the highlighted run up to the crosshair (bright).
        # Downsampling + clipping keep long ranges cheap to redraw while zooming.
        self._line_item = pg.PlotDataItem()
        self._line_item.setDownsampling(auto=True, method="peak")
        self._line_item.setClipToView(True)
        self._line_hi = pg.PlotDataItem()
        self._line_hi.setDownsampling(auto=True, method="peak")
        self._line_hi.setClipToView(True)
        self._candle_item = CandlestickItem()
        self._baseline = pg.InfiniteLine(angle=0, movable=False)
        self._vline = pg.InfiniteLine(angle=90, movable=False)
        self._dot = pg.ScatterPlotItem(size=9, pen=pg.mkPen(None))
        # Anchored top-centre and hung just inside the top edge, so the
        # caption sits under the ceiling instead of being clipped by it.
        self._time_label = pg.TextItem(anchor=(0.5, 0.0))
        # Where the series ends, and what it ends at — the two things you look
        # for first on a price chart.
        self._last_dot = pg.ScatterPlotItem(size=8, pen=pg.mkPen(None))
        self._last_label = pg.TextItem(anchor=(1.05, 0.5))
        for item in (self._baseline, self._line_item, self._line_hi, self._candle_item,
                     self._last_dot, self._last_label,
                     self._vline, self._dot, self._time_label):
            self._plot.addItem(item)
        self._vline.hide()
        self._dot.hide()
        self._time_label.hide()
        self._baseline.hide()
        self._last_dot.hide()
        self._last_label.hide()
        self._plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

        self._empty_label = QLabel("No chart data available")
        self._empty_label.setObjectName("Faint")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()

        grid.addWidget(self._plot, 0, 0)
        grid.addWidget(self._empty_label, 0, 0, Qt.AlignmentFlag.AlignCenter)
        root.addLayout(grid, 1)

        # -- range selector, under the chart (as on Robinhood) -------------- #
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 10, 0, 0)
        bar.setSpacing(0)

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

        # Both of these come and go (the readout follows the cursor, the reset
        # button only exists while zoomed), so they live in fixed-width holders —
        # otherwise the Line/Candles buttons slide sideways as you use the chart.
        self._hover_label = QLabel("")
        self._hover_label.setObjectName("Faint")
        self._hover_label.setStyleSheet("font-size: 11px;")
        self._hover_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._hover_label.setFixedWidth(330)
        bar.addWidget(self._hover_label)
        bar.addSpacing(12)

        self._reset_btn = QPushButton("Reset zoom")
        self._reset_btn.setObjectName("Ghost")
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.clicked.connect(self.reset_zoom)
        self._reset_btn.hide()
        reset_holder = QWidget()
        reset_box = QHBoxLayout(reset_holder)
        reset_box.setContentsMargins(0, 0, 0, 0)
        reset_box.addWidget(self._reset_btn)
        reset_holder.setFixedWidth(104)
        bar.addWidget(reset_holder)
        bar.addSpacing(8)

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

        rule = QFrame()
        rule.setObjectName("Divider")
        rule.setFixedHeight(1)
        root.addWidget(rule)

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
            self._hover_index = None
            self._hover_label.setText("")
            self._cache_series()
            # Clear immediately: leaving the previous symbol's line on screen
            # while the new one loads shows the wrong data under the new name.
            self._render()

    def update_reference(self, previous_close: float | None) -> None:
        """Provide the previous close so a 1D baseline can be drawn."""
        self._prev_close = previous_close
        if self._mode == "line":
            self._render()

    def set_candles(self, symbol: str, range_key: str, candles: list[Candle]) -> None:
        """Receive a fresh candle series (from the feed) and redraw."""
        if symbol != self._symbol or range_key != self._range:
            # Stale payload for a symbol/range the user has already moved past.
            return
        self._candles = candles
        self._cache_series()
        self._render()

    def _cache_series(self) -> None:
        """Mirror the candle list into numpy arrays for the zoom hot path."""
        if not self._candles:
            self._xs = self._closes = self._lows = self._highs = np.empty(0)
            return
        self._xs = np.fromiter((c.epoch for c in self._candles), float, len(self._candles))
        self._closes = np.fromiter((c.close for c in self._candles), float, len(self._candles))
        self._lows = np.fromiter((c.low for c in self._candles), float, len(self._candles))
        self._highs = np.fromiter((c.high for c in self._candles), float, len(self._candles))

    def reset_zoom(self) -> None:
        """Frame the whole series again after the user has zoomed in."""
        if not len(self._xs):
            return
        self._suppress_autoscale = True
        self._plot.setXRange(float(self._xs[0]), float(self._xs[-1]), padding=0.02)
        self._suppress_autoscale = False
        self._autoscale_y(force=True)

    def apply_theme(self) -> None:
        c = theme.chart_colors()
        self._plot.setBackground(c["background"])
        for axis in ("bottom", "left"):
            ax = self._plot.getAxis(axis)
            ax.setPen(pg.mkPen(c["axis"]))
            ax.setTextPen(pg.mkPen(c["text"]))
            ax.setStyle(tickLength=4, tickTextOffset=6)
        self._plot.getAxis("left").setWidth(62)
        self._baseline.setPen(
            pg.mkPen(c["baseline"], width=1, style=Qt.PenStyle.DotLine))
        self._vline.setPen(pg.mkPen(c["crosshair"], width=1))
        self._time_label.setColor(c["text"])
        self._sync_axis_visibility()
        self._render()

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _sync_axis_visibility(self) -> None:
        """Both views carry a price and time scale.

        A bare line is prettier, but you cannot read a level off it: the price
        axis and faint horizontal rules are what let you see *where* the line is,
        not just its shape.
        """
        self._plot.showGrid(x=False, y=True, alpha=0.16)
        for axis in ("bottom", "left"):
            self._plot.showAxis(axis, show=True)

    def _trend_colors(self) -> tuple[str, str]:
        """(bright, dim) colours for the current series direction.

        Measured against the previous close on a 1D chart — the same reference
        the quoted day change uses — so the line's colour and the headline
        percentage can never disagree. Longer ranges compare first to last bar.
        """
        colors = theme.chart_colors()
        closes = self._closes
        if not len(closes):
            return colors["up"], colors["up_dim"]
        reference = (self._prev_close if self._range == "1D" and self._prev_close
                     else float(closes[0]))
        rising = float(closes[-1]) >= reference
        if rising:
            return colors["up"], colors["up_dim"]
        return colors["down"], colors["down_dim"]

    def _render(self) -> None:
        has_data = bool(self._candles)
        self._empty_label.setVisible(not has_data)
        colors = theme.chart_colors()

        if not has_data:
            self._draw_anim.stop()
            self._drawing = False
            self._line_item.clear()
            self._line_hi.clear()
            self._candle_item.clear()
            self._baseline.hide()
            self._last_dot.hide()
            self._last_label.hide()
            self._hide_crosshair()
            self._sync_reset_button()
            return

        xs, closes = self._xs, self._closes
        bright, dim = self._trend_colors()

        key = (self._symbol, self._range, self._mode, len(self._candles))
        is_new = key[:3] != (self._last_key[:3] if self._last_key else None)

        if self._mode == "candles":
            self._draw_anim.stop()
            self._drawing = False
            self._line_item.hide()
            self._line_hi.hide()
            self._candle_item.show()
            self._candle_item.set_data(self._candles, colors["up"], colors["down"])
        else:
            self._candle_item.hide()
            self._line_item.show()
            pen = pg.mkPen(dim, width=2)
            self._draw_params = (xs, closes, pen)
            if is_new and anim.ENABLED and len(xs) > 3:
                self._start_draw()          # Robinhood-style left-to-right reveal
            elif not self._drawing:
                self._set_line(xs, closes, pen)
            self._line_hi.setPen(pg.mkPen(bright, width=2))
            self._refresh_highlight()

        # 1D baseline at the previous close (Robinhood-style reference line).
        if self._range == "1D" and self._prev_close:
            self._baseline.setValue(self._prev_close)
            self._baseline.show()
        else:
            self._baseline.hide()

        # Frame the data only when the dataset (symbol/range/mode) changes, so a
        # manual zoom survives the periodic refresh.
        if is_new:
            self._suppress_autoscale = True
            self._plot.setXRange(float(xs[0]), float(xs[-1]), padding=0.02)
            self._suppress_autoscale = False
            self._hide_crosshair()
        span = float(xs[-1] - xs[0]) or 1.0
        self._plot.getViewBox().setLimits(
            xMin=float(xs[0]) - span * 0.05, xMax=float(xs[-1]) + span * 0.05)
        # A periodic data refresh shouldn't re-snap a view the user has zoomed;
        # only a genuinely new dataset re-frames unconditionally.
        self._autoscale_y(force=is_new)
        self._show_last_price(bright)
        self._last_key = key

    def _show_last_price(self, color: str) -> None:
        """Mark where the series ends and print the level next to it."""
        if not len(self._xs):
            self._last_dot.hide()
            self._last_label.hide()
            return
        x = float(self._xs[-1])
        y = float(self._closes[-1])
        self._last_dot.setData([x], [y], brush=pg.mkBrush(color), pen=pg.mkPen(None))
        self._last_label.setText(fmt_price(y))
        self._last_label.setColor(color)
        self._last_dot.show()
        self._last_label.show()
        self._place_last_label()

    def _place_last_label(self) -> None:
        """Pin the price tag to the right edge of the *view*.

        Hanging it off the end of the data put it outside the visible range,
        where it was clipped to a stray dollar sign.
        """
        if self._last_label.isVisible() and len(self._closes):
            (_x0, x1), _ = self._plot.getViewBox().viewRange()
            self._last_label.setPos(float(x1), float(self._closes[-1]))

    # ------------------------------------------------------------------ #
    # Zoom: x is user-driven, y always refits the visible window
    # ------------------------------------------------------------------ #
    def _on_x_range_changed(self, *_args) -> None:
        self._place_last_label()
        if self._suppress_autoscale:
            return
        self._autoscale_y()

    def _visible_bounds(self, x0: float, x1: float) -> tuple[float, float, int]:
        """(low, high, count) of the samples inside ``[x0, x1]``.

        Uses ``searchsorted`` on the cached arrays — the candle list is never
        walked here, because this runs on every step of a wheel zoom.
        """
        xs = self._xs
        left = int(np.searchsorted(xs, x0, side="left"))
        right = int(np.searchsorted(xs, x1, side="right"))
        if right <= left:  # zoomed between two bars: keep the nearest one
            left = max(0, min(left, len(xs) - 1))
            right = left + 1
        if self._mode == "candles":
            lo = float(self._lows[left:right].min())
            hi = float(self._highs[left:right].max())
        else:
            window = self._closes[left:right]
            lo = float(window.min())
            hi = float(window.max())
        return lo, hi, right - left

    def _fit_price_window(self, x0: float, x1: float) -> tuple[float, float] | None:
        """The price window that frames ``[x0, x1]`` — the one source of truth
        for both the wheel's atomic zoom and the range-changed fallback."""
        if not len(self._xs):
            return None
        lo, hi, count = self._visible_bounds(float(x0), float(x1))
        # Keep the previous-close reference in frame while the whole series is
        # shown; once zoomed in, the visible bars alone drive the scale.
        if self._range == "1D" and self._prev_close and count == len(self._xs):
            lo = min(lo, self._prev_close)
            hi = max(hi, self._prev_close)
        pad = (hi - lo) * 0.08 or max(abs(hi) * 0.002, 0.01)
        window = (lo - pad, hi + pad)
        self._last_y = window          # so the fallback's dead-band sees it
        self._sync_reset_button(float(x1) - float(x0))
        return window

    def _autoscale_y(self, force: bool = False) -> None:
        """Fit the price axis to the candles currently on screen.

        This is what makes zooming *mean* something: without it the y range stays
        pinned to the whole series and a zoomed-in candle view is a flat ribbon
        in the middle of an empty chart.

        A dead-band keeps the fit from chasing sub-pixel float noise, which is
        what makes a continuous zoom feel like it is stuttering.
        """
        if not len(self._xs):
            return
        (x0, x1), (cur0, cur1) = self._plot.getViewBox().viewRange()
        previous = self._last_y
        target = self._fit_price_window(float(x0), float(x1))
        if target is None:
            return
        # The wheel path already applied this window; re-applying it would cost a
        # second redraw per notch. Skip when nothing meaningfully moved.
        if not force:
            span = max(target[1] - target[0], 1e-9)
            drift = max(abs(target[0] - float(cur0)), abs(target[1] - float(cur1)))
            if drift < span * 0.002:
                return
            if previous is not None:
                moved = max(abs(target[0] - previous[0]), abs(target[1] - previous[1]))
                if moved < span * 0.002 and drift < span * 0.01:
                    return
        self._suppress_autoscale = True
        self._plot.setYRange(target[0], target[1], padding=0)
        self._suppress_autoscale = False

    def _sync_reset_button(self, visible_span: float | None = None) -> None:
        """Offer a way back once the view no longer shows the whole series.

        Toggling a button forces a layout pass, so only do it on a real change —
        otherwise every wheel tick re-lays-out the control row under the chart.
        """
        if not len(self._xs):
            should_show = False
        else:
            if visible_span is None:
                (x0, x1), _ = self._plot.getViewBox().viewRange()
                visible_span = float(x1 - x0)
            full = float(self._xs[-1] - self._xs[0]) or 1.0
            should_show = visible_span < full * 0.98
        if should_show != self._reset_visible:
            self._reset_visible = should_show
            self._reset_btn.setVisible(should_show)

    # ------------------------------------------------------------------ #
    # Line draw-in animation
    # ------------------------------------------------------------------ #
    def _set_line(self, xs, closes, pen, k: int | None = None) -> None:
        """Draw the line using the first ``k`` points (all of them if ``k`` is None)."""
        if k is None or k >= len(xs):
            self._line_item.setData(xs, closes, pen=pen)
        else:
            k = max(2, k)
            self._line_item.setData(xs[:k], closes[:k], pen=pen)

    def _start_draw(self) -> None:
        self._draw_anim.stop()
        self._drawing = True
        xs, closes, pen = self._draw_params
        self._set_line(xs, closes, pen, k=2)  # seed
        self._draw_anim.start()

    def _on_draw_step(self, frac) -> None:
        if not self._drawing or self._draw_params is None:
            return
        xs, closes, pen = self._draw_params
        self._set_line(xs, closes, pen, k=int(float(frac) * len(xs)))

    def _on_draw_done(self) -> None:
        self._drawing = False
        if self._draw_params is not None:
            xs, closes, pen = self._draw_params
            self._set_line(xs, closes, pen)

    # ------------------------------------------------------------------ #
    # Interaction
    # ------------------------------------------------------------------ #
    def _on_range_clicked(self, range_key: str) -> None:
        self._range = range_key
        self._candles = []
        self._last_key = None
        self._hide_crosshair()
        self.rangeChanged.emit(range_key)

    def _on_mode_clicked(self, mode: str) -> None:
        self._mode = mode
        self._last_key = None
        self._hide_crosshair()
        self._sync_axis_visibility()
        self._render()

    def _hide_crosshair(self) -> None:
        self._hover_index = None
        self._vline.hide()
        self._dot.hide()
        self._time_label.hide()
        self._line_hi.hide()
        self._hover_label.setText("")

    def _refresh_highlight(self) -> None:
        """Light up the line from the left edge to the crosshair."""
        idx = self._hover_index
        if idx is None or self._mode != "line" or idx < 1:
            self._line_hi.hide()
            return
        # Slices of the cached arrays — a fresh array per mouse move would make
        # hovering a long series visibly chuggy.
        self._line_hi.setData(self._xs[: idx + 1], self._closes[: idx + 1])
        self._line_hi.show()

    def _hover_time_text(self, candle: Candle) -> str:
        local = candle.time.astimezone()
        if self._range in _INTRADAY_RANGES:
            return local.strftime("%-I:%M %p")
        return local.strftime("%b %d, %Y")

    def _on_mouse_moved(self, pos) -> None:
        if not self._candles:
            return
        vb = self._plot.getViewBox()
        if not self._plot.sceneBoundingRect().contains(pos):
            self._hide_crosshair()
            return
        point = vb.mapSceneToView(pos)
        # The series is sorted by time, so the nearest bar is a binary search
        # rather than a scan of every point.
        xs = self._xs
        pos_x = float(point.x())
        right = int(np.searchsorted(xs, pos_x))
        if right <= 0:
            idx = 0
        elif right >= len(xs):
            idx = len(xs) - 1
        else:
            idx = right if (xs[right] - pos_x) < (pos_x - xs[right - 1]) else right - 1
        candle = self._candles[idx]
        bright, _dim = self._trend_colors()

        self._hover_index = idx
        self._vline.setValue(candle.epoch)
        self._vline.show()

        if self._mode == "line":
            self._dot.setData([candle.epoch], [candle.close],
                              brush=pg.mkBrush(bright), pen=pg.mkPen(None))
            self._dot.show()
            self._refresh_highlight()
        else:
            self._dot.hide()
            self._line_hi.hide()

        # Time caption floating above the crosshair, as on the reference chart.
        (_x0, _x1), (y0, y1) = vb.viewRange()
        self._time_label.setText(self._hover_time_text(candle))
        self._time_label.setPos(candle.epoch, y1 - (y1 - y0) * 0.012)
        self._time_label.show()

        color = theme.color_for(candle.close - candle.open)
        self._hover_label.setText(
            f"O {fmt_price(candle.open)}   H {fmt_price(candle.high)}   "
            f"L {fmt_price(candle.low)}   "
            f"<span style='color:{color}'>C {fmt_price(candle.close)}</span>"
            + (f"   V {fmt_compact(candle.volume)}" if candle.volume else "")
        )

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._hide_crosshair()
        super().leaveEvent(event)
