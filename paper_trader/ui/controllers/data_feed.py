"""Background market-data feed.

A :class:`DataFeed` is a QObject moved onto a dedicated ``QThread``. It owns a
``QTimer`` that ticks every ``FEED_TICK_SECONDS`` and, on each tick, refreshes
the active symbol's quote (and periodically its chart and the watchlist) via the
:class:`MarketDataService`. Results are delivered to the GUI thread through Qt
signals, so the UI never blocks on the network and updates are naturally
serialised onto the event loop (flicker-free).

Configuration coming *from* the GUI thread (active symbol, chart range,
watchlist) is written under a mutex; an immediate out-of-band refresh can be
requested when the user changes symbol so they don't wait for the next tick.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import (
    QMetaObject,
    QMutex,
    QMutexLocker,
    QObject,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)

from ...config import (
    CHART_REFRESH_EVERY_TICKS,
    DEFAULT_RANGE,
    FEED_TICK_SECONDS,
    SPARKLINE_POINTS,
    SPARKLINE_REFRESH_SECONDS,
    SPARKLINES_PER_TICK,
    WATCHLIST_REFRESH_EVERY_TICKS,
)
from ...data.market_data import (
    InvalidSymbolError,
    MarketDataError,
    MarketDataService,
    NetworkError,
)


class DataFeed(QObject):
    """Polls market data on a worker thread and emits results to the GUI."""

    # Payloads are plain Python objects (Quote / list[Candle] / dict[str, Quote]).
    quoteReady = pyqtSignal(object)                 # Quote for the active symbol
    chartReady = pyqtSignal(str, str, object)       # symbol, range_key, list[Candle]
    watchlistQuotesReady = pyqtSignal(object)       # dict[symbol, Quote]
    sparklinesReady = pyqtSignal(object)            # dict[symbol, list[float]]
    errorOccurred = pyqtSignal(str, str)            # (kind, message)

    def __init__(
        self,
        service: MarketDataService,
        active: str = "",
        range_key: str = DEFAULT_RANGE,
        watchlist: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._mutex = QMutex()
        self._active = active.upper()
        self._range = range_key
        self._watchlist = [s.upper() for s in (watchlist or [])]
        self._force_chart = True
        self._tick_count = 0
        self._timer: QTimer | None = None
        self._running = False
        self._spark_at: dict[str, float] = {}   # symbol -> when its line was fetched
        self._spark_paused_until = 0.0           # back off after a failed fetch

    # ------------------------------------------------------------------ #
    # Lifecycle (run in the worker thread)
    # ------------------------------------------------------------------ #
    @pyqtSlot()
    def start(self) -> None:
        """Create the timer and perform the first fetch (called on thread start)."""
        self._timer = QTimer()
        self._timer.setInterval(int(FEED_TICK_SECONDS * 1000))
        self._timer.timeout.connect(self._tick)
        self._running = True
        self._timer.start()
        self._tick()  # don't wait a full interval for the first data

    @pyqtSlot()
    def stop(self) -> None:
        self._running = False
        if self._timer is not None:
            self._timer.stop()

    # ------------------------------------------------------------------ #
    # Thread-safe configuration (called from the GUI thread)
    # ------------------------------------------------------------------ #
    def set_active_symbol(self, symbol: str) -> None:
        with QMutexLocker(self._mutex):
            self._active = symbol.upper()
            self._force_chart = True

    def set_range(self, range_key: str) -> None:
        with QMutexLocker(self._mutex):
            self._range = range_key
            self._force_chart = True

    def set_watchlist(self, symbols: list[str]) -> None:
        with QMutexLocker(self._mutex):
            self._watchlist = [s.upper() for s in symbols]

    def request_immediate(self) -> None:
        """Ask the worker thread to refresh the active symbol right now."""
        QMetaObject.invokeMethod(self, "_immediate", Qt.ConnectionType.QueuedConnection)

    # ------------------------------------------------------------------ #
    # Fetch cycle (worker thread only)
    # ------------------------------------------------------------------ #
    @pyqtSlot()
    def _immediate(self) -> None:
        active, range_key, _wl, _fc = self._snapshot()
        self._fetch_active(active, range_key, want_chart=True)

    def _snapshot(self) -> tuple[str, str, list[str], bool]:
        with QMutexLocker(self._mutex):
            return self._active, self._range, list(self._watchlist), self._force_chart

    def _clear_force_chart(self) -> None:
        with QMutexLocker(self._mutex):
            self._force_chart = False

    @pyqtSlot()
    def _tick(self) -> None:
        if not self._running:
            return
        self._tick_count += 1
        active, range_key, watchlist, force_chart = self._snapshot()

        want_chart = force_chart or (self._tick_count % CHART_REFRESH_EVERY_TICKS == 0)
        if active:
            self._fetch_active(active, range_key, want_chart=want_chart)
            if force_chart:
                self._clear_force_chart()

        # Refresh the watchlist on the first tick and periodically thereafter.
        if watchlist and (self._tick_count % WATCHLIST_REFRESH_EVERY_TICKS == 1):
            self._fetch_watchlist(watchlist)
        if watchlist:
            self._fetch_sparklines(watchlist)

    def _fetch_active(self, symbol: str, range_key: str, want_chart: bool) -> None:
        if not symbol:
            return
        try:
            if want_chart:
                quote, candles = self._service.get_quote_and_candles(symbol, range_key)
                self.quoteReady.emit(quote)
                self.chartReady.emit(symbol, range_key, candles)
            else:
                quote = self._service.get_quote(symbol)
                self.quoteReady.emit(quote)
        except InvalidSymbolError as exc:
            self.errorOccurred.emit("symbol", str(exc))
        except NetworkError as exc:
            self.errorOccurred.emit("network", str(exc))
        except MarketDataError as exc:
            self.errorOccurred.emit("data", str(exc))
        except Exception as exc:  # never let the worker thread die on a bad tick
            self.errorOccurred.emit("data", f"Unexpected data error: {exc}")

    def _fetch_watchlist(self, symbols: list[str]) -> None:
        # One batched request where the provider supports it (Alpaca), otherwise
        # the service loops internally. Cached symbols (e.g. the active one) are
        # served without any network call.
        try:
            quotes = self._service.get_quotes(symbols)
        except NetworkError:
            self.errorOccurred.emit("network", "Could not refresh the watchlist.")
            return
        except MarketDataError:
            return
        except Exception:
            return
        if quotes:
            self.watchlistQuotesReady.emit(quotes)

    def _fetch_sparklines(self, symbols: list[str]) -> None:
        """Refresh the watchlist's day-lines, oldest first, a couple per tick.

        Best-effort decoration: failures are swallowed so a provider that can't
        serve charts never spams the status line — and one failure pauses the
        whole thing for a cycle, because a timing-out provider would otherwise
        hold up the live quotes on this same thread on every tick.
        """
        now = time.monotonic()
        if now < self._spark_paused_until:
            return
        due = [s for s in symbols
               if now - self._spark_at.get(s, float("-inf")) >= SPARKLINE_REFRESH_SECONDS]
        if not due:
            return
        due.sort(key=lambda s: self._spark_at.get(s, float("-inf")))
        # Symbols that have never had a line get one straight away (a new
        # window shouldn't fill in two rows at a time); refreshes trickle.
        fresh = sum(1 for s in due if s not in self._spark_at)
        batch = max(SPARKLINES_PER_TICK, min(fresh, 8))
        lines: dict[str, list[float]] = {}
        for symbol in due[:batch]:
            self._spark_at[symbol] = now
            try:
                candles = self._service.get_candles(symbol, "1D")
            except Exception:
                self._spark_paused_until = now + SPARKLINE_REFRESH_SECONDS
                break
            closes = [c.close for c in candles]
            if len(closes) > 1:
                lines[symbol] = _thin(closes, SPARKLINE_POINTS)
        if lines:
            self.sparklinesReady.emit(lines)


def _thin(values: list[float], points: int) -> list[float]:
    """Evenly spaced samples of ``values`` (first and last kept)."""
    n = len(values)
    if n <= points:
        return list(values)
    step = (n - 1) / (points - 1)
    return [values[round(i * step)] for i in range(points)]
