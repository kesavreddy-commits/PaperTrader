"""Interactive price chart (line + candlestick) built on pyqtgraph.

The line view is deliberately chrome-free — no axes, no grid, just the price
line, a dotted previous-close reference and a crosshair — which is what makes a
Robinhood-style page read as a *price*, not a plot. It also borrows the
reference's colouring: on the 1D chart each market session (pre-market,
regular hours, after hours) is its own stretch of line; past sessions are drawn
in a quiet olive, the live one bright (lime while it is an extended session),
and whichever session is under the cursor lights up. A live 1D chart spans the
whole trading day, so the line stops where "now" is. Longer ranges draw the
series bright and, under the cursor, keep only the run up to it lit.

Scrubbing publishes the hovered price through :attr:`pointHovered`, so the
page's big price can follow the cursor the way the reference does; the
candlestick view adds an OHLC readout on the plot itself.

The candlestick view is the analytical counterpart: it fills exactly the same
(large) area and turns the axes back on so the bars can be read against a price
scale. Zoom behaviour is shared by both views. The wheel zooms the *time* axis
and the price axis then refits itself to whatever is on screen, so zooming in
genuinely magnifies the candles instead of squashing them into a flat band.

pyqtgraph is used for its speed: updates go through ``setData`` on persistent
plot items, so refreshes are allocation-light and flicker-free. The candlestick
renderer is a small custom ``GraphicsObject`` that paints once into a
``QPicture`` and blits it — the standard high-performance pyqtgraph pattern.

The widget is a pure view: it holds the candle series it was given and redraws
on range/type changes, but it performs no network I/O. Range-button clicks are
surfaced via the :attr:`rangeChanged` signal for the owner to act on.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

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
from PyQt6.QtGui import QFont, QPainter, QPicture
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
from .segments import segment_group

pg.setConfigOptions(antialias=True)

# Ranges whose bars are intraday — used to pick the hover-label time format.
_INTRADAY_RANGES = {"1D", "1W"}

# The exchange's day, in minutes after midnight Eastern.
_ET = ZoneInfo("America/New_York")
_REGULAR_OPEN = 9 * 60 + 30
_REGULAR_CLOSE = 16 * 60
_EXTENDED_CLOSE = 20 * 60


def session_runs(xs: np.ndarray) -> list[tuple[int, int, str]]:
    """Split a day of bars into runs by market session: ``(first, last, kind)``.

    ``kind`` is ``"pre"`` (before 9:30 ET), ``"regular"`` or ``"post"`` (from
    16:00 ET). Runs are contiguous index ranges, so a series that crosses
    midnight gets a fresh run for the new day.
    """
    if not len(xs):
        return []
    # One offset for the whole series: a 1D chart never spans a DST change
    # during trading hours.
    offset = datetime.fromtimestamp(float(xs[-1]), _ET).utcoffset().total_seconds()
    local = xs + offset
    minutes = (local % 86400) // 60
    day = local // 86400
    kind = np.where(minutes < _REGULAR_OPEN, 0, np.where(minutes < _REGULAR_CLOSE, 1, 2))
    breaks = np.flatnonzero((np.diff(kind) != 0) | (np.diff(day) != 0)) + 1
    starts = np.concatenate(([0], breaks))
    ends = np.concatenate((breaks - 1, [len(xs) - 1]))
    names = ("pre", "regular", "post")
    return [(int(s), int(e), names[int(kind[s])]) for s, e in zip(starts, ends)]


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

    rangeChanged = pyqtSignal(str)    # emitted when the user picks a new range
    pointHovered = pyqtSignal(object)  # hovered close (float), or None on leave

    # A floor, not a target: the chart takes all the height the splitter gives
    # it, but may shrink to this on a small window rather than squeezing its
    # neighbours off the screen.
    MIN_PLOT_HEIGHT = 190

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
        # Chart chrome, driven by the Theme menu. Bare by default: the line view
        # is a price, not a plot. Candles are the exception — bars are unreadable
        # without a scale to read them against.
        self._show_axes = False
        self._show_grid = False
        self._show_last_price = True
        # The series is mirrored into numpy arrays: the zoom handler runs on
        # every wheel step and must not walk a Python list of candles each time.
        self._xs = np.empty(0)
        self._closes = np.empty(0)
        self._lows = np.empty(0)
        self._highs = np.empty(0)
        self._last_y: tuple[float, float] | None = None
        # Session colouring (1D): the runs, which one is live, which is hovered.
        self._runs: list[tuple[int, int, str]] = []
        self._segmented = False
        self._hover_run: int | None = None
        self._frame: tuple[float, float] | None = None   # the "whole chart" x-range
        self._live_color = ""
        self._hi_color = ""
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
        self._plot.setFrameShape(QFrame.Shape.NoFrame)
        vb = self._plot.getViewBox()
        vb.setDefaultPadding(0.0)
        vb.sigXRangeChanged.connect(self._on_x_range_changed)

        # Three layers of one line: the whole series in its resting colour, the
        # live stretch over it, and whatever the cursor lights up on top.
        # Downsampling + clipping keep long ranges cheap to redraw while zooming.
        self._line_item = pg.PlotDataItem()
        self._line_live = pg.PlotDataItem()
        self._line_hi = pg.PlotDataItem()
        for item in (self._line_item, self._line_live, self._line_hi):
            item.setDownsampling(auto=True, method="peak")
            item.setClipToView(True)
        self._candle_item = CandlestickItem()
        self._baseline = pg.InfiniteLine(angle=0, movable=False)
        self._vline = pg.InfiniteLine(angle=90, movable=False)
        self._dot = pg.ScatterPlotItem(size=10)
        # Anchored top-centre and hung just inside the top edge, so the
        # caption sits under the ceiling instead of being clipped by it.
        self._time_label = pg.TextItem(anchor=(0.5, 0.0))
        # Where the series ends, and what it ends at — the two things you look
        # for first on a price chart.
        self._last_dot = pg.ScatterPlotItem(size=8, pen=pg.mkPen(None))
        # A filled tag, so the level stays readable where it overlaps the line.
        self._last_label = pg.TextItem(anchor=(1.05, 0.5), ensureInBounds=True)
        for item in (self._baseline, self._line_item, self._line_live, self._line_hi,
                     self._candle_item, self._last_dot, self._last_label,
                     self._vline, self._dot, self._time_label):
            self._plot.addItem(item)
        self._line_live.hide()
        self._vline.hide()
        self._dot.hide()
        self._time_label.hide()
        self._baseline.hide()
        self._last_dot.hide()
        self._last_label.hide()
        self._line_hi.hide()
        self._plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

        # The candle view's OHLC readout rides on the plot's top-left corner.
        self._hover_label = QLabel("", self._plot)
        self._hover_label.setObjectName("Clear")
        self._hover_label.setTextFormat(Qt.TextFormat.RichText)
        self._hover_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._hover_label.setFont(theme.tabular(self._hover_label.font()))
        self._hover_label.hide()

        self._empty_label = QLabel("No chart data available")
        self._empty_label.setObjectName("Faint")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()

        grid.addWidget(self._plot, 0, 0)
        grid.addWidget(self._empty_label, 0, 0, Qt.AlignmentFlag.AlignCenter)
        root.addLayout(grid, 1)

        # -- range selector, under the chart (as on the reference) ----------- #
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 16, 0, 0)
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
            bar.addWidget(btn, 0, Qt.AlignmentFlag.AlignBottom)
            if key == self._range:
                btn.setChecked(True)
        bar.addStretch(1)

        # Only exists while zoomed. It sits at the row's far end with nothing
        # after it, so coming and going never shifts the tabs.
        self._reset_btn = QPushButton("Reset zoom")
        self._reset_btn.setObjectName("Ghost")
        self._reset_btn.setFixedHeight(28)
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.clicked.connect(self.reset_zoom)
        self._reset_btn.hide()
        bar.addWidget(self._reset_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(bar)

        # The Line / Candles switch belongs to the chart but is placed by the
        # owner (beside the price hero): eight range tabs plus a toggle don't
        # fit one row of a narrow window.
        self.mode_toggle, buttons = segment_group(
            ("Line", "Candles"), checked=0 if self._mode == "line" else 1)
        self._type_group = self.mode_toggle._segment_group
        for btn, mode in zip(buttons, ("line", "candles")):
            btn.clicked.connect(lambda _=False, m=mode: self._on_mode_clicked(m))
        root.addSpacing(12)

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
            self._hide_crosshair()
            self._cache_series()
            # Clear immediately: leaving the previous symbol's line on screen
            # while the new one loads shows the wrong data under the new name.
            self._render()

    def update_reference(self, previous_close: float | None) -> None:
        """Provide the previous close so a 1D baseline can be drawn."""
        if previous_close == self._prev_close:
            return
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

    def reference_price(self) -> float | None:
        """What the range's change is measured from: the previous close on 1D,
        the first bar's open on longer ranges (the reference's "Past week")."""
        if self._range == "1D":
            if self._prev_close:
                return self._prev_close
        if self._candles:
            first = self._candles[0]
            return first.open or first.close
        return None

    def set_accent(self, name: str) -> None:
        """Follow the stock's day (``"up"``/``"down"``) in the range tabs' hover."""
        for btn in self._range_group.buttons():
            theme.set_accent(btn, name)

    def _cache_series(self) -> None:
        """Mirror the candle list into numpy arrays for the zoom hot path."""
        if not self._candles:
            self._xs = self._closes = self._lows = self._highs = np.empty(0)
            return
        self._xs = np.fromiter((c.epoch for c in self._candles), float, len(self._candles))
        self._closes = np.fromiter((c.close for c in self._candles), float, len(self._candles))
        self._lows = np.fromiter((c.low for c in self._candles), float, len(self._candles))
        self._highs = np.fromiter((c.high for c in self._candles), float, len(self._candles))

    def set_chrome(self, *, axes: bool | None = None, grid: bool | None = None,
                   last_price: bool | None = None) -> None:
        """Turn the axes, gridlines and last-price tag on or off."""
        if axes is not None:
            self._show_axes = bool(axes)
        if grid is not None:
            self._show_grid = bool(grid)
        if last_price is not None:
            self._show_last_price = bool(last_price)
        self._sync_axis_visibility()
        self._render()

    def chrome(self) -> dict[str, bool]:
        return {"axes": self._show_axes, "grid": self._show_grid,
                "last_price": self._show_last_price}

    def reset_zoom(self) -> None:
        """Frame the whole series again after the user has zoomed in."""
        if not len(self._xs):
            return
        self._suppress_autoscale = True
        self._apply_frame()
        self._suppress_autoscale = False
        self._autoscale_y(force=True)

    def apply_theme(self) -> None:
        c = theme.chart_colors()
        self._plot.setBackground(c["background"])
        tick_font = QFont(theme.ui_font_family())
        tick_font.setPixelSize(11)
        for axis in ("bottom", "left"):
            ax = self._plot.getAxis(axis)
            ax.setPen(pg.mkPen(c["axis"]))
            ax.setTextPen(pg.mkPen(c["text"]))
            ax.setStyle(tickLength=4, tickTextOffset=6, tickFont=tick_font)
        self._plot.getAxis("left").setWidth(64)
        # Round dots, spaced out, as the reference draws its previous close.
        dots = pg.mkPen(c["baseline"], width=1.6)
        dots.setCapStyle(Qt.PenCapStyle.RoundCap)
        dots.setDashPattern([0.01, 3.4])
        self._baseline.setPen(dots)
        label_font = QFont(theme.ui_font_family())
        label_font.setPixelSize(12)
        label_font.setWeight(QFont.Weight.DemiBold)
        self._last_label.setFont(label_font)
        self._last_label.fill = pg.mkBrush(c["tag_fill"])
        self._last_label.border = pg.mkPen(c["tag_border"])
        self._vline.setPen(pg.mkPen(c["crosshair"], width=1))
        self._time_label.setFont(label_font)
        self._time_label.setColor(c["time_text"])
        # Filled with the page so the crosshair never strikes through the time.
        self._time_label.fill = pg.mkBrush(c["background"])
        self._dot.setPen(pg.mkPen(c["background"], width=2))
        # A backing in the page colour keeps the figures legible over candles.
        bg = theme.chart_colors()["background"]
        self._hover_label.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {theme.color('text_muted')};"
            f" background-color: {bg}; border-radius: 4px; padding: 2px 6px;")
        self._sync_axis_visibility()
        self._last_key = None      # re-apply pens and colours on the next render
        self._render()

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _sync_axis_visibility(self) -> None:
        """Show as much chart furniture as the current settings ask for.

        The line view defaults to bare — no axes, no grid, just the price, its
        previous-close reference and the crosshair. The candlestick view always
        keeps its scales: you cannot read bars without them.
        """
        candles = self._mode == "candles"
        show = self._show_axes or candles
        self._plot.showGrid(x=False, y=self._show_grid, alpha=0.16)
        for axis in ("bottom", "left"):
            self._plot.showAxis(axis, show=show)

    def _rising(self) -> bool:
        """Is the series up? Measured against the previous close on a 1D chart —
        the same reference the quoted day change uses — so the line's colour and
        the headline percentage can never disagree. Longer ranges compare the
        last bar with the first."""
        closes = self._closes
        if not len(closes):
            return True
        reference = (self._prev_close if self._range == "1D" and self._prev_close
                     else float(closes[0]))
        return float(closes[-1]) >= reference

    def _trend_colors(self) -> tuple[str, str]:
        """(bright, dim) colours for the current series direction."""
        colors = theme.chart_colors()
        if self._rising():
            return colors["up"], colors["up_dim"]
        return colors["down"], colors["down_dim"]

    def _ext_color(self) -> str:
        colors = theme.chart_colors()
        return colors["up_ext"] if self._rising() else colors["down_ext"]

    def _render(self) -> None:
        has_data = bool(self._candles)
        self._empty_label.setVisible(not has_data)

        if not has_data:
            self._draw_anim.stop()
            self._drawing = False
            for item in (self._line_item, self._line_live, self._line_hi):
                item.clear()
            self._line_live.hide()
            self._candle_item.clear()
            self._baseline.hide()
            self._last_dot.hide()
            self._last_label.hide()
            self._runs = []
            self._segmented = False
            self._frame = None
            self._hide_crosshair()
            self._sync_reset_button()
            return

        colors = theme.chart_colors()
        xs, closes = self._xs, self._closes
        bright, dim = self._trend_colors()

        key = (self._symbol, self._range, self._mode, len(self._candles))
        is_new = key[:3] != (self._last_key[:3] if self._last_key else None)

        # Sessions (1D only): a day with extended hours is drawn session by
        # session; a single regular session is just the line.
        self._runs = session_runs(xs) if self._range == "1D" else []
        self._segmented = any(kind != "regular" for _s, _e, kind in self._runs)
        if self._segmented:
            live_kind = self._runs[-1][2]
            self._live_color = bright if live_kind == "regular" else self._ext_color()
        else:
            self._live_color = bright
        self._hover_run = None

        if self._mode == "candles":
            self._draw_anim.stop()
            self._drawing = False
            for item in (self._line_item, self._line_live, self._line_hi):
                item.hide()
            self._candle_item.show()
            self._candle_item.set_data(self._candles, colors["up"], colors["down"])
        else:
            self._candle_item.hide()
            self._line_item.show()
            pen = pg.mkPen(dim, width=2)
            self._line_item.setPen(pen)
            self._line_live.setPen(pg.mkPen(self._live_color, width=2))
            self._line_hi.setPen(pg.mkPen(bright, width=2))
            self._hi_color = bright
            self._draw_params = (xs, closes, pen)
            if is_new and anim.ENABLED and len(xs) > 3:
                self._start_draw()          # Robinhood-style left-to-right reveal
            elif not self._drawing:
                self._reveal(None)
            self._refresh_highlight()

        # 1D baseline at the previous close (Robinhood-style reference line).
        if self._range == "1D" and self._prev_close:
            self._baseline.setValue(self._prev_close)
            self._baseline.show()
        else:
            self._baseline.hide()

        # Frame the data only when the dataset (symbol/range/mode) changes, so a
        # manual zoom survives the periodic refresh.
        self._frame = self._frame_bounds()
        if is_new:
            self._suppress_autoscale = True
            self._apply_frame()
            self._suppress_autoscale = False
            self._hide_crosshair()
        x0, x1 = self._frame
        span = (x1 - x0) or 1.0
        self._plot.getViewBox().setLimits(xMin=x0 - span * 0.05, xMax=x1 + span * 0.05)
        # A periodic data refresh shouldn't re-snap a view the user has zoomed;
        # only a genuinely new dataset re-frames unconditionally.
        self._autoscale_y(force=is_new)
        self._draw_last_price(self._live_color if self._mode == "line" else bright)
        self._sync_reset_button()
        self._last_key = key

    def _frame_bounds(self) -> tuple[float, float]:
        """The x-range that shows "the whole chart".

        A live 1D chart runs to the end of the trading day (8pm ET with
        extended hours, else the 4pm close), so the line ends where "now" is —
        the reference's look. Anything else is framed by its own data.
        """
        first, last = float(self._xs[0]), float(self._xs[-1])
        end = last
        if self._range == "1D" and len(self._xs) > 1:
            first_et = datetime.fromtimestamp(first, _ET)
            last_et = datetime.fromtimestamp(last, _ET)
            if first_et.date() == last_et.date() and first_et.hour >= 4:
                close = _EXTENDED_CLOSE if self._segmented else _REGULAR_CLOSE
                session_end = last_et.replace(hour=close // 60, minute=close % 60,
                                              second=0, microsecond=0).timestamp()
                end = max(last, session_end)
        return first, end

    def _apply_frame(self) -> None:
        x0, x1 = self._frame or (float(self._xs[0]), float(self._xs[-1]))
        span = (x1 - x0) or 1.0
        # Flush left like the reference; a sliver on the right keeps the end
        # dot off the edge when the data itself ends the frame.
        right = span * 0.012 if x1 <= float(self._xs[-1]) else 0.0
        self._plot.setXRange(x0, x1 + right, padding=0)

    def _draw_last_price(self, color: str) -> None:
        """Mark where the series ends and print the level next to it."""
        if not len(self._xs) or not self._show_last_price:
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
        """Put the price tag beside the line's end, or pin it to the view's edge.

        On a live day there is open space after the line, so the tag sits just
        past the last point. When the line runs to the edge (or the end is
        scrolled out of view) it pins to the right edge of the *view* — hung off
        the data there, it was clipped to a stray dollar sign.
        """
        if not (self._show_last_price and self._last_label.isVisible() and len(self._closes)):
            return
        vb = self._plot.getViewBox()
        (x0, x1), _ = vb.viewRange()
        last_x = float(self._xs[-1])
        y = float(self._closes[-1])
        px_w = vb.viewPixelSize()[0] or 0.0
        tag_w = self._last_label.boundingRect().width() * px_w
        gap = 10 * px_w
        if x0 <= last_x and last_x + gap + tag_w * 1.1 < x1:
            self._last_label.setAnchor((0.0, 0.5))
            self._last_label.setPos(last_x + gap, y)
        else:
            self._last_label.setAnchor((1.05, 0.5))
            self._last_label.setPos(float(x1), y)

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
        if right <= left:  # zoomed between two bars (or past the end): nearest one
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
        """Offer a way back once the view no longer shows the whole chart.

        Toggling a button forces a layout pass, so only do it on a real change —
        otherwise every wheel tick re-lays-out the control row under the chart.
        """
        if not len(self._xs):
            should_show = False
        else:
            if visible_span is None:
                (x0, x1), _ = self._plot.getViewBox().viewRange()
                visible_span = float(x1 - x0)
            f0, f1 = self._frame or (float(self._xs[0]), float(self._xs[-1]))
            full = float(f1 - f0) or 1.0
            should_show = visible_span < full * 0.98
        if should_show != self._reset_visible:
            self._reset_visible = should_show
            self._reset_btn.setVisible(should_show)

    # ------------------------------------------------------------------ #
    # Line draw-in animation
    # ------------------------------------------------------------------ #
    def _reveal(self, k: int | None) -> None:
        """Draw the first ``k`` points of every line layer (all of them if None)."""
        xs, closes = self._xs, self._closes
        n = len(xs)
        k = n if k is None else max(2, min(int(k), n))
        self._line_item.setData(xs[:k], closes[:k])
        if self._segmented:
            start, end, _kind = self._runs[-1]
            start = max(0, start - 1)            # join the previous session
            stop = min(end + 1, k)
            if stop - start >= 2:
                self._line_live.setData(xs[start:stop], closes[start:stop])
            else:
                self._line_live.clear()
        else:
            self._line_live.setData(xs[:k], closes[:k])
        self._line_live.setVisible(self._mode == "line" and self._hover_index is None)

    def _set_line(self, xs, closes, pen, k: int | None = None) -> None:
        """Draw the line using the first ``k`` points (all of them if ``k`` is None)."""
        self._reveal(k)

    def _start_draw(self) -> None:
        self._draw_anim.stop()
        self._drawing = True
        self._reveal(2)  # seed
        self._draw_anim.start()

    def _on_draw_step(self, frac) -> None:
        if not self._drawing or self._draw_params is None:
            return
        self._reveal(int(float(frac) * len(self._xs)))

    def _on_draw_done(self) -> None:
        self._drawing = False
        if self._draw_params is not None and len(self._xs):
            self._reveal(None)

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
        for btn in self._type_group.buttons():
            btn.setChecked(btn.text().lower() == mode)
        self._last_key = None
        self._hide_crosshair()
        self._sync_axis_visibility()
        self._render()

    def _hide_crosshair(self) -> None:
        was_hovering = self._hover_index is not None
        self._hover_index = None
        self._hover_run = None
        self._vline.hide()
        self._dot.hide()
        self._time_label.hide()
        self._line_hi.hide()
        self._hover_label.setText("")
        self._hover_label.hide()
        if self._mode == "line" and len(self._xs) and not self._drawing:
            self._line_live.show()
        if was_hovering:
            self.pointHovered.emit(None)

    def _run_at(self, idx: int) -> int:
        for i, (start, end, _kind) in enumerate(self._runs):
            if start <= idx <= end:
                return i
        return len(self._runs) - 1

    def _refresh_highlight(self) -> None:
        """Light up what the cursor is over: its session on a 1D chart, the run
        from the left edge to the crosshair on longer ranges."""
        idx = self._hover_index
        if idx is None or self._mode != "line":
            self._line_hi.hide()
            return
        self._line_live.hide()
        if self._segmented:
            run = self._run_at(idx)
            if run != self._hover_run or not self._line_hi.isVisible():
                start, end, _kind = self._runs[run]
                start = max(0, start - 1)
                self._line_hi.setData(self._xs[start:end + 1], self._closes[start:end + 1])
                self._hover_run = run
        else:
            if idx < 1:
                self._line_hi.hide()
                return
            # Slices of the cached arrays — a fresh array per mouse move would
            # make hovering a long series visibly chuggy.
            self._line_hi.setData(self._xs[: idx + 1], self._closes[: idx + 1])
        self._line_hi.show()

    def _hover_time_text(self, candle: Candle) -> str:
        local = candle.time.astimezone()
        if self._range in _INTRADAY_RANGES:
            text = local.strftime("%I:%M %p").lstrip("0")
            return text if self._range == "1D" else f"{local.strftime('%b %d')}, {text}"
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
        # rather than a scan of every point. Past the last bar (a live day's
        # empty afternoon) the crosshair rests on the latest one.
        xs = self._xs
        pos_x = float(point.x())
        right = int(np.searchsorted(xs, pos_x))
        if right <= 0:
            idx = 0
        elif right >= len(xs):
            idx = len(xs) - 1
        else:
            idx = right if (xs[right] - pos_x) < (pos_x - xs[right - 1]) else right - 1
        if idx == self._hover_index and self._vline.isVisible():
            return
        candle = self._candles[idx]

        self._hover_index = idx
        self._vline.setValue(candle.epoch)
        self._vline.show()

        if self._mode == "line":
            self._dot.setData([candle.epoch], [candle.close],
                              brush=pg.mkBrush(self._hi_color or self._live_color))
            self._dot.show()
            self._refresh_highlight()
            self._hover_label.hide()
        else:
            self._dot.hide()
            self._line_hi.hide()
            color = theme.color_for(candle.close - candle.open)
            muted = theme.color("text_muted")
            text = theme.color("text")
            cells = [("O", fmt_price(candle.open)), ("H", fmt_price(candle.high)),
                     ("L", fmt_price(candle.low))]
            html = (f"<span style='color:{text}'>{self._hover_time_text(candle)}</span>"
                    "&nbsp;&nbsp;&nbsp;&nbsp;")
            html += "&nbsp;&nbsp;&nbsp;".join(
                f"<span style='color:{muted}'>{k}</span>&nbsp;"
                f"<span style='color:{text}'>{v}</span>" for k, v in cells)
            html += (f"&nbsp;&nbsp;&nbsp;<span style='color:{muted}'>C</span>&nbsp;"
                     f"<span style='color:{color}'>{fmt_price(candle.close)}</span>")
            if candle.volume:
                html += (f"&nbsp;&nbsp;&nbsp;<span style='color:{muted}'>Vol</span>&nbsp;"
                         f"<span style='color:{text}'>{fmt_compact(candle.volume)}</span>")
            self._hover_label.setText(html)
            self._hover_label.adjustSize()
            # Inside the plotting area, clear of the price axis.
            corner = self._plot.mapFromScene(vb.sceneBoundingRect().topLeft())
            self._hover_label.move(corner.x() + 6, corner.y() + 4)
            self._hover_label.show()
            self._hover_label.raise_()

        # Time caption floating above the crosshair, as on the reference chart
        # (the candle view prints the time in its readout instead).
        if self._mode == "line":
            (_x0, _x1), (y0, y1) = vb.viewRange()
            self._time_label.setText(self._hover_time_text(candle))
            self._time_label.setPos(candle.epoch, y1 - (y1 - y0) * 0.012)
            self._time_label.show()
        else:
            self._time_label.hide()
        self.pointHovered.emit(float(candle.close))

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._hide_crosshair()
        super().leaveEvent(event)
