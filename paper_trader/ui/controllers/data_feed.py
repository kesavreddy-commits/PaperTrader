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
