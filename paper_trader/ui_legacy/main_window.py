"""The pre-rework main window — composition root of the legacy UI.

Same wiring as the current window (it owns the session, the broker, the market
data service and the two background feeds) but the earlier layout: a portfolio
card across the top, a three-pane splitter of watchlist | chart | order ticket,
and a toolbar rather than a nav bar. Symbol search lives in the watchlist.

Kept runnable via ``python run.py --old``. The behavioural fixes from the rework
(clearing option positions on reset, not wiping fill notices, validating typed
symbols) are carried over — this is the old *layout*, not the old bugs.
"""

from __future__ import annotations

from PyQt6.QtCore import (
    QMetaObject,
    QObject,
    QRunnable,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..broker.alpaca import AlpacaBroker
from ..broker.base import Broker, BrokerError
from ..broker.local import LocalBroker
from ..config import APP_NAME, AUTOSAVE_SECONDS
from ..core.models import Session, Side
from ..core.options import OptionContract, price_contract
from ..credentials import load_alpaca
from ..data.alpaca_client import AlpacaClient
from ..data.market_data import MarketDataError, MarketDataService, create_service
from ..persistence.store import Store, StoreError
from ..ui import anim, theme
from ..ui.controllers.broker_feed import BrokerFeed
from ..ui.controllers.data_feed import DataFeed
from ..ui.dialogs import (
    AlpacaKeysDialog,
    AnalyticsDialog,
    NewSessionDialog,
    OpenSessionDialog,
)
from ..ui.format import fmt_shares
from .widgets.history_table import HistoryTable, OrdersTable
from .widgets.option_ticket import OptionOrderTicket, OptionTicket
from .widgets.options_chain import OptionsChainView
from .widgets.options_positions import OptionsPositionsTable
from .widgets.positions_table import PositionsTable
from .widgets.chart import ChartWidget
from .widgets.portfolio_bar import PortfolioBar
from .widgets.price_header import PriceHeader
from .widgets.trade_panel import OrderTicket, TradePanel
from .widgets.watchlist import WatchlistPanel


# --------------------------------------------------------------------------- #
# Off-thread symbol search / validation
# --------------------------------------------------------------------------- #
class _SearchSignals(QObject):
    done = pyqtSignal(str, object)


class _ValidateSignals(QObject):
    done = pyqtSignal(str, object, str)


class _SearchTask(QRunnable):
    def __init__(self, service: MarketDataService, query: str, signals: _SearchSignals) -> None:
        super().__init__()
        self._service = service
        self._query = query
        self._signals = signals

    def run(self) -> None:
        try:
            results = self._service.search(self._query)
        except Exception:
            results = []
        self._signals.done.emit(self._query, results)


class _ValidateTask(QRunnable):
    def __init__(self, service: MarketDataService, symbol: str,
                 signals: _ValidateSignals) -> None:
        super().__init__()
        self._service = service
        self._symbol = symbol
        self._signals = signals

    def run(self) -> None:
        try:
            quote = self._service.validate_symbol(self._symbol)
        except MarketDataError as exc:
            self._signals.done.emit(self._symbol, None, str(exc))
        except Exception as exc:
            self._signals.done.emit(self._symbol, None, f"Lookup failed: {exc}")
        else:
            self._signals.done.emit(self._symbol, quote, "")


# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self, session: Session, store: Store, broker_mode: str = "local",
                 data_source: str = "demo", theme_name: str = "dark") -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} (classic)")
        self.resize(1440, 900)
        self.setMinimumSize(1080, 700)

        self._store = store
        self.session = session
        self._theme_name = theme_name
        self._broker_mode = broker_mode
        self._data_source = data_source

        # Backends.
        self.broker: Broker = self._make_broker(broker_mode)
        self.market: MarketDataService = self._make_market(data_source)

        # Live state (GUI thread).
        self._prices: dict[str, float] = {}
        self._prev_closes: dict[str, float] = {}
        self._active_symbol = ""
        self._active_quote = None
        self._feed_symbols: list[str] = []
        self._market_mode = "stock"           # "stock" | "options"
        self._selected_contract: OptionContract | None = None
        self._buying_power = 0.0
        self._status_is_error = False

        # Search plumbing.
        self._search_signals = _SearchSignals()
        self._search_signals.done.connect(self._on_search_done)
        self._validate_signals = _ValidateSignals()
        self._validate_signals.done.connect(self._on_validate_done)
        self._pending_query = ""
        self._pool = QThreadPool.globalInstance()

        # Thread handles.
        self._thread: QThread | None = None
        self._feed: DataFeed | None = None
        self._broker_thread: QThread | None = None
        self._broker_feed: BrokerFeed | None = None

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._bind_session(session, initial=True)
        self._sync_options_availability()
        self._start_feed()
        self._start_broker_feed()

        self._autosave = QTimer(self)
        self._autosave.setInterval(int(AUTOSAVE_SECONDS * 1000))
        self._autosave.timeout.connect(lambda: self._save(silent=True))
        self._autosave.start()

    # ================================================================== #
    # Backend construction
    # ================================================================== #
    def _make_broker(self, mode: str) -> Broker:
        if mode == "alpaca":
            creds = load_alpaca()
            if creds is not None:
                try:
                    return AlpacaBroker(AlpacaClient(creds))
                except Exception:
                    pass
            self._broker_mode = "local"
        return LocalBroker(self.session)

    def _make_market(self, source: str) -> MarketDataService:
        try:
            return create_service(source)
        except MarketDataError:
            self._data_source = "demo"
            return create_service("demo")

    # ================================================================== #
    # UI construction
    # ================================================================== #
    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        self._portfolio_bar = PortfolioBar()
        outer.addWidget(self._portfolio_bar)

        self._watchlist = WatchlistPanel()
        self._watchlist.symbolSelected.connect(self.set_active_symbol)
        self._watchlist.watchlistChanged.connect(self._on_watchlist_changed)
        self._watchlist.searchRequested.connect(self._on_search_requested)
        self._watchlist.searchSubmitted.connect(self._on_symbol_submitted)

        self._price_header = PriceHeader()
        self._chart = ChartWidget()
        self._chart.rangeChanged.connect(self._on_range_changed)
        self._options_chain = OptionsChainView()
        self._options_chain.contractSelected.connect(self._on_contract_selected)

        center = QWidget()
        center_box = QVBoxLayout(center)
        center_box.setContentsMargins(0, 0, 0, 0)
        center_box.setSpacing(12)
        center_box.addWidget(_panel(self._price_header))
        center_box.addLayout(self._build_mode_toggle())

        # Chart (stock) / chain (options) swap in the centre.
        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(_panel(self._chart))          # 0: stock
        self._center_stack.addWidget(_panel(self._options_chain))  # 1: options
        center_box.addWidget(self._center_stack, 1)

        self._trade_panel = TradePanel()
        self._trade_panel.orderRequested.connect(self._on_order_requested)
        self._option_ticket = OptionTicket()
        self._option_ticket.orderRequested.connect(self._on_option_order_requested)

        # Trade panel (stock) / option ticket (options) swap on the right.
        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(_panel(self._trade_panel))     # 0: stock
        self._right_stack.addWidget(_panel(self._option_ticket))   # 1: options

        top_split = QSplitter(Qt.Orientation.Horizontal)
        top_split.addWidget(_panel(self._watchlist))
        top_split.addWidget(center)
        top_split.addWidget(self._right_stack)
        top_split.setStretchFactor(0, 0)
        top_split.setStretchFactor(1, 1)
        top_split.setStretchFactor(2, 0)
        top_split.setSizes([270, 800, 320])
        top_split.setChildrenCollapsible(False)

        self._positions = PositionsTable()
        self._positions.symbolSelected.connect(self.set_active_symbol)
        self._positions.sellAllRequested.connect(self._sell_all)
        self._options_positions = OptionsPositionsTable()
        self._options_positions.closeRequested.connect(self._close_option)
        self._options_positions.underlyingSelected.connect(self.set_active_symbol)
        self._history = HistoryTable()
        self._orders = OrdersTable()
        self._orders.cancelRequested.connect(self._cancel_order)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._positions, "Positions")
        self._tabs.addTab(self._options_positions, "Options")
        self._tabs.addTab(self._history, "History")
        self._tabs.addTab(self._orders, "Orders")
        self._tabs.setMinimumHeight(132)

        main_split = QSplitter(Qt.Orientation.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(_panel(self._tabs))
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 0)
        main_split.setSizes([580, 260])
        main_split.setChildrenCollapsible(False)
        outer.addWidget(main_split, 1)

        self.setCentralWidget(central)
        self._source_label = QLabel("")
        self.statusBar().addPermanentWidget(self._source_label)
        self.statusBar().setSizeGripEnabled(False)
        self._update_source_label()

    def _build_mode_toggle(self) -> QHBoxLayout:
        """The Stock / Options segmented control above the centre stack."""
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(2, 0, 2, 0)
        self._mode_group = QButtonGroup(self)
        self._stock_mode_btn = _mode_segment("  Stock  ", checked=True)
        self._options_mode_btn = _mode_segment("  Options  ")
        self._stock_mode_btn.clicked.connect(lambda: self._set_market_mode("stock"))
        self._options_mode_btn.clicked.connect(lambda: self._set_market_mode("options"))
        for b in (self._stock_mode_btn, self._options_mode_btn):
            self._mode_group.addButton(b)
            row.addWidget(b)
        row.addStretch(1)
        return row

    # ================================================================== #
    # Market mode (stock / options)
    # ================================================================== #
    def _set_market_mode(self, mode: str) -> None:
        if mode == "options" and not self.broker.supports_options:
            resp = QMessageBox.question(
                self, "Trade options",
                "Options trading runs on the Local simulator. Switch your "
                "trading account to it now?\n\nYour Alpaca account is untouched — "
                "switch back anytime from Account → Trading Account.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if resp != QMessageBox.StandardButton.Yes:
                self._stock_mode_btn.setChecked(True)
                self._options_mode_btn.setChecked(False)
                return
            self._set_broker_mode("local")
            if not self.broker.supports_options:
                self._stock_mode_btn.setChecked(True)
                self._options_mode_btn.setChecked(False)
                return
            self._status("Switched to the Local simulator to trade options.", 5000)
        self._market_mode = mode
        is_opt = mode == "options"
        anim.cross_fade_stack(self._center_stack, 1 if is_opt else 0)
        anim.cross_fade_stack(self._right_stack, 1 if is_opt else 0)
        self._stock_mode_btn.setChecked(not is_opt)
        self._options_mode_btn.setChecked(is_opt)
        if is_opt:
            self._options_chain.set_underlying(
                self._active_symbol, self._prices.get(self._active_symbol))
            self._refresh_option_ticket()

    def _sync_options_availability(self) -> None:
        self._trade_panel.set_extended_hours_supported(self.broker.is_remote)
        ok = self.broker.supports_options
        self._options_mode_btn.setToolTip(
            "" if ok else "Options trade on the Local simulator — click to switch.")
        if not ok and self._market_mode == "options":
            self._set_market_mode("stock")

    def _on_contract_selected(self, contract: OptionContract, quote) -> None:
        self._selected_contract = contract
        pos_qty = self.broker.option_position_quantity(contract.occ_symbol)
        self._option_ticket.set_contract(contract, quote, pos_qty)
        self._option_ticket.set_underlying_price(self._prices.get(contract.underlying))
        self._option_ticket.set_account(self._buying_power, pos_qty)
        anim.cross_fade_stack(self._right_stack, 1)

    def _refresh_option_ticket(self) -> None:
        c = self._selected_contract
        if c is None:
            return
        price = self._prices.get(c.underlying)
        if price and price > 0:
            self._option_ticket.update_quote(price_contract(c, price))
        self._option_ticket.set_underlying_price(price)
        self._option_ticket.set_account(
            self._buying_power, self.broker.option_position_quantity(c.occ_symbol))

    # ================================================================== #
    # Menus / toolbar
    # ================================================================== #
    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        _add(file_menu, "&New Local Session…", self._new_session, "Ctrl+N")
        _add(file_menu, "&Open Local Session…", self._open_session, "Ctrl+O")
        _add(file_menu, "&Rename Session…", self._rename_session)
        _add(file_menu, "Rese&t Local Session…", self._reset_session)
        file_menu.addSeparator()
        _add(file_menu, "&Save Now", lambda: self._save(silent=False), "Ctrl+S")
        file_menu.addSeparator()
        _add(file_menu, "&Quit", self.close, "Ctrl+Q")

        acct_menu = bar.addMenu("&Account")
        broker_menu = acct_menu.addMenu("&Trading Account")
        self._broker_group = QActionGroup(self)
        for label, mode in (("Alpaca paper account", "alpaca"), ("Local simulator", "local")):
            act = QAction(label, self, checkable=True)
            act.setMenuRole(QAction.MenuRole.NoRole)
            act.setChecked(mode == self._broker_mode)
            act.triggered.connect(lambda _=False, m=mode: self._set_broker_mode(m))
            self._broker_group.addAction(act)
            broker_menu.addAction(act)
        _add(acct_menu, "Alpaca API &Keys…", self._edit_keys)
        acct_menu.addSeparator()
        _add(acct_menu, "&Analytics…", self._show_analytics, "Ctrl+A")

        view_menu = bar.addMenu("&View")
        _add(view_menu, "Toggle &Dark / Light", self._toggle_theme, "Ctrl+D")
        source_menu = view_menu.addMenu("Market &Data Source")
        self._source_group = QActionGroup(self)
        for label, source in (("Alpaca (IEX)", "alpaca"), ("Yahoo Finance", "yahoo"),
                              ("Demo (offline)", "demo")):
            act = QAction(label, self, checkable=True)
            act.setMenuRole(QAction.MenuRole.NoRole)
            act.setChecked(source == self._data_source)
            act.triggered.connect(lambda _=False, s=source: self._set_data_source(s))
            self._source_group.addAction(act)
            source_menu.addAction(act)

    def _build_toolbar(self) -> None:
        """An always-visible in-window toolbar so key actions don't depend on the
        native menu bar (which macOS puts at the top of the screen)."""
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(tb)

        keys_act = QAction("🔑  Alpaca API Keys…", self)
        keys_act.setMenuRole(QAction.MenuRole.NoRole)
        keys_act.setToolTip("Enter or update your Alpaca API key and secret")
        keys_act.triggered.connect(lambda: self._edit_keys())
        tb.addAction(keys_act)
        tb.addSeparator()

        acct_btn = QToolButton(self)
        acct_btn.setText("Trading Account ▾")
        acct_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        acct_menu = QMenu(acct_btn)
        acct_menu.addActions(self._broker_group.actions())
        acct_btn.setMenu(acct_menu)
        tb.addWidget(acct_btn)

        data_btn = QToolButton(self)
        data_btn.setText("Market Data ▾")
        data_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        data_menu = QMenu(data_btn)
        data_menu.addActions(self._source_group.actions())
        data_btn.setMenu(data_menu)
        tb.addWidget(data_btn)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        analytics_act = QAction("Analytics…", self)
        analytics_act.setMenuRole(QAction.MenuRole.NoRole)
        analytics_act.triggered.connect(lambda: self._show_analytics())
        tb.addAction(analytics_act)

        theme_act = QAction("Theme", self)
        theme_act.setMenuRole(QAction.MenuRole.NoRole)
        theme_act.triggered.connect(lambda: self._toggle_theme())
        tb.addAction(theme_act)

    # ================================================================== #
    # Session binding
    # ================================================================== #
    def _bind_session(self, session: Session, initial: bool = False) -> None:
        self.session = session
        if isinstance(self.broker, LocalBroker):
            self.broker.bind(session)
        self._prices.clear()
        self._prev_closes.clear()
        self._active_quote = None

        self._watchlist.set_watchlist(session.watchlist)
        held = self.broker.held_symbols()
        first = session.watchlist[0] if session.watchlist else (held[0] if held else "AAPL")
        self._active_symbol = first
        self._price_header.show_placeholder(first)
        self._chart.set_symbol(first)
        self._trade_panel.set_symbol(first)
        self._watchlist.set_active(first)
        self._selected_contract = None
        self._option_ticket.clear_contract()
        self._options_chain.set_underlying(first, None)

        self._refresh_portfolio()
        self._refresh_history()
        self.setWindowTitle(f"{APP_NAME} (classic) — {session.name}")
        if not initial:
            self._sync_feed_symbols(force=True)
            if self._feed is not None:
                self._feed.set_active_symbol(first)
                self._feed.set_range(self._chart.current_range())
                self._feed.request_immediate()

    # ================================================================== #
    # Feed lifecycle
    # ================================================================== #
    def _start_feed(self) -> None:
        self._thread = QThread(self)
        self._feed = DataFeed(self.market, active=self._active_symbol,
                              range_key=self._chart.current_range(),
                              watchlist=self._feed_symbol_list())
        self._feed.moveToThread(self._thread)
        self._thread.started.connect(self._feed.start)
        self._feed.quoteReady.connect(self._on_quote)
        self._feed.chartReady.connect(self._on_chart)
        self._feed.watchlistQuotesReady.connect(self._on_watchlist_quotes)
        self._feed.errorOccurred.connect(self._on_feed_error)
        self._thread.start()

    def _stop_feed(self) -> None:
        if self._feed is not None and self._thread is not None and self._thread.isRunning():
            try:
                QMetaObject.invokeMethod(self._feed, "stop", Qt.ConnectionType.QueuedConnection)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.quit()
            if not self._thread.wait(4000):
                self._thread.terminate()
                self._thread.wait(500)
        self._thread = None
        self._feed = None

    def _start_broker_feed(self) -> None:
        if not self.broker.is_remote:
            return
        self._broker_thread = QThread(self)
        self._broker_feed = BrokerFeed(self.broker)
        self._broker_feed.moveToThread(self._broker_thread)
        self._broker_thread.started.connect(self._broker_feed.start)
        self._broker_feed.updated.connect(self._on_broker_updated)
        self._broker_feed.errorOccurred.connect(self._on_broker_error)
        self._broker_thread.start()

    def _stop_broker_feed(self) -> None:
        if self._broker_feed is not None and self._broker_thread is not None \
                and self._broker_thread.isRunning():
            try:
                QMetaObject.invokeMethod(self._broker_feed, "stop",
                                         Qt.ConnectionType.QueuedConnection)
            except Exception:
                pass
        if self._broker_thread is not None:
            self._broker_thread.quit()
            if not self._broker_thread.wait(4000):
                self._broker_thread.terminate()
                self._broker_thread.wait(500)
        self._broker_thread = None
        self._broker_feed = None

    # ================================================================== #
    # Active symbol
    # ================================================================== #
    def set_active_symbol(self, symbol: str) -> None:
        symbol = symbol.strip().upper()
        if not symbol:
            return
        self._active_symbol = symbol
        self._active_quote = None
        self._price_header.show_placeholder(symbol)
        self._chart.set_symbol(symbol)
        self._trade_panel.set_symbol(symbol)
        self._trade_panel.set_market_price(self._prices.get(symbol))
        self._watchlist.set_active(symbol)
        self._options_chain.set_underlying(symbol, self._prices.get(symbol))
        if self._selected_contract is not None and self._selected_contract.underlying != symbol:
            self._selected_contract = None
            self._option_ticket.clear_contract()
        self._tabs.setCurrentIndex(0)
        self._refresh_portfolio()
        if self._feed is not None:
            self._feed.set_active_symbol(symbol)
            self._feed.set_range(self._chart.current_range())
            self._feed.request_immediate()

    # ================================================================== #
    # Market-feed slots
    # ================================================================== #
    def _on_quote(self, quote) -> None:
        self._prices[quote.symbol] = quote.price
        self._prev_closes[quote.symbol] = quote.previous_close
        if quote.symbol == self._active_symbol:
            self._active_quote = quote
            self._price_header.update_quote(quote)
            self._chart.update_reference(quote.previous_close)
            self._trade_panel.set_market_price(quote.price)
            self._trade_panel.set_session(quote.market_state)
            self._options_chain.set_price(quote.price)
        self._watchlist.update_quotes({quote.symbol: quote})
        self._clear_error_status()
        self._on_prices_changed()

    def _on_chart(self, symbol: str, range_key: str, candles) -> None:
        self._chart.set_candles(symbol, range_key, candles)

    def _on_watchlist_quotes(self, quotes: dict) -> None:
        for sym, quote in quotes.items():
            self._prices[sym] = quote.price
            self._prev_closes[sym] = quote.previous_close
        self._watchlist.update_quotes(quotes)
        self._on_prices_changed()

    def _on_feed_error(self, kind: str, message: str) -> None:
        self._status(f"⚠ {message}", 6000, error=True)

    def _on_prices_changed(self) -> None:
        saved = False
        for msg in self.broker.on_price_tick(self._prices):
            self._status(msg, 6000)
            self._refresh_history()
            saved = True
        for msg in self.broker.settle_options(self._prices):
            self._status(msg, 8000)
            self._refresh_history()
            saved = True
        if saved:
            self._save(silent=True)
        self._refresh_portfolio()

    # ================================================================== #
    # Broker-feed slots
    # ================================================================== #
    def _on_broker_updated(self) -> None:
        self._refresh_portfolio()
        self._refresh_history()
        self._sync_feed_symbols()

    def _on_broker_error(self, message: str) -> None:
        hint = "  Check Account → Alpaca API Keys." if "denied" in message or "auth" \
            in message.lower() else ""
        self._status(f"⚠ Alpaca: {message}{hint}", 8000, error=True)

    # ================================================================== #
    # Status bar
    # ================================================================== #
    def _status(self, message: str, msecs: int = 5000, *, error: bool = False) -> None:
        self._status_is_error = error
        self.statusBar().showMessage(message, msecs)

    def _clear_error_status(self) -> None:
        if self._status_is_error:
            self._status_is_error = False
            self.statusBar().clearMessage()

    # ================================================================== #
    # Refresh
    # ================================================================== #
    def _refresh_portfolio(self) -> None:
        snap = self.broker.snapshot(self._prices, self._prev_closes)
        self._buying_power = snap.buying_power
        self._portfolio_bar.update_snapshot(snap, self._account_label())
        self._positions.update_positions(snap.positions)
        self._options_positions.update_positions(snap.option_positions)
        self._trade_panel.set_account(
            snap.buying_power, self.broker.position_quantity(self._active_symbol))
        self._refresh_option_ticket()
        if not self.broker.is_remote:
            self.session.record_equity(snap.total_value)
        self._sync_feed_symbols()

    def _refresh_history(self) -> None:
        self._history.update_trades(self.broker.recent_trades())
        self._orders.update_orders(self.broker.open_orders())

    def _account_label(self) -> str:
        if self.broker.is_remote:
            return f"{self.session.name}  ·  Alpaca paper"
        return self.session.name

    # ================================================================== #
    # Feed symbol set (watchlist ∪ holdings)
    # ================================================================== #
    def _feed_symbol_list(self) -> list[str]:
        seen = dict.fromkeys(self.session.watchlist)
        if self._active_symbol:
            seen[self._active_symbol] = None
        for sym in self.broker.held_symbols():
            seen[sym] = None
        for sym in self.broker.option_underlyings():
            seen[sym] = None
        return list(seen)

    def _sync_feed_symbols(self, force: bool = False) -> None:
        symbols = self._feed_symbol_list()
        if force or symbols != self._feed_symbols:
            self._feed_symbols = symbols
            if self._feed is not None:
                self._feed.set_watchlist(symbols)

    # ================================================================== #
    # Trading
    # ================================================================== #
    def _on_order_requested(self, ticket: OrderTicket) -> None:
        symbol = self._active_symbol
        if not symbol:
            return
        price = self._prices.get(symbol)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if ticket.order_type == "LIMIT":
                if not ticket.limit_price or ticket.limit_price <= 0:
                    raise BrokerError("Enter a valid limit price.")
                qty = ticket.value / ticket.limit_price if ticket.mode == "DOLLARS" else ticket.value
                side = Side.BUY if ticket.side == "BUY" else Side.SELL
                msg = self.broker.place_limit(symbol, side, qty, ticket.limit_price,
                                              extended_hours=ticket.extended_hours)
            elif ticket.side == "BUY":
                msg = self.broker.buy_market(
                    symbol, price,
                    quantity=None if ticket.mode == "DOLLARS" else ticket.value,
                    notional=ticket.value if ticket.mode == "DOLLARS" else None)
            else:
                msg = self.broker.sell_market(
                    symbol, price,
                    quantity=None if ticket.mode == "DOLLARS" else ticket.value,
                    notional=ticket.value if ticket.mode == "DOLLARS" else None)
        except BrokerError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Order rejected", str(exc))
            return
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Order error", f"Unexpected error: {exc}")
            return
        QApplication.restoreOverrideCursor()

        self._trade_panel.clear_amount()
        self._status(msg, 6000)
        self._after_trade()

    def _sell_all(self, symbol: str) -> None:
        owned = self.broker.position_quantity(symbol)
        if owned <= 0:
            return
        if QMessageBox.question(
            self, "Sell all",
            f"Sell your entire position of {fmt_shares(owned)} {symbol}?"
        ) != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            msg = self.broker.sell_market(symbol, self._prices.get(symbol), sell_all=True)
        except BrokerError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Order rejected", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self._status(msg, 6000)
        self._after_trade()

    def _on_option_order_requested(self, ticket: OptionOrderTicket) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if ticket.side == "BUY":
                msg = self.broker.buy_option(
                    ticket.contract, ticket.quantity, ticket.price, self._prices)
            else:
                msg = self.broker.sell_option(
                    ticket.contract, ticket.quantity, ticket.price, self._prices)
        except BrokerError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Order rejected", str(exc))
            return
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Order error", f"Unexpected error: {exc}")
            return
        QApplication.restoreOverrideCursor()
        self._status(msg, 6000)
        self._after_trade()
        self._refresh_option_ticket()

    def _close_option(self, occ_symbol: str) -> None:
        qty = self.broker.option_position_quantity(occ_symbol)
        if not qty:
            return
        contract = OptionContract.parse(occ_symbol)
        price = self._prices.get(contract.underlying)
        if not price or price <= 0:
            QMessageBox.information(
                self, "Close position",
                "Waiting for a live price on the underlying to value this contract.")
            return
        side_word = "long" if qty > 0 else "short"
        if QMessageBox.question(
            self, "Close position",
            f"Close your {side_word} position of {int(abs(qty))} contract(s)?\n"
            f"{contract.description}"
        ) != QMessageBox.StandardButton.Yes:
            return
        quote = price_contract(contract, price)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if qty > 0:   # long → sell to close at the bid
                msg = self.broker.sell_option(contract, abs(qty), quote.bid, self._prices)
            else:         # short → buy to close at the ask
                msg = self.broker.buy_option(contract, abs(qty), quote.ask, self._prices)
        except BrokerError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Close failed", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self._status(msg, 6000)
        self._after_trade()
        self._refresh_option_ticket()

    def _cancel_order(self, order_id: str) -> None:
        try:
            self.broker.cancel_order(order_id)
        except BrokerError as exc:
            QMessageBox.warning(self, "Cancel failed", str(exc))
            return
        self._after_trade()

    def _after_trade(self) -> None:
        engine = getattr(self.broker, "engine", None)
        if engine is not None:
            engine.prune_inactive_orders()
        self._refresh_portfolio()
        self._refresh_history()
        if not self.broker.is_remote:
            self._save(silent=True)
        elif self._broker_feed is not None:
            self._broker_feed.request_immediate()

    # ================================================================== #
    # Watchlist / search / range
    # ================================================================== #
    def _on_watchlist_changed(self, symbols: list) -> None:
        self.session.watchlist = [s.upper() for s in symbols]
        self._sync_feed_symbols(force=True)
        self._save(silent=True)

    def _on_range_changed(self, range_key: str) -> None:
        if self._feed is not None:
            self._feed.set_range(range_key)
            self._feed.request_immediate()

    def _on_search_requested(self, query: str) -> None:
        self._pending_query = query
        self._pool.start(_SearchTask(self.market, query, self._search_signals))

    def _on_search_done(self, query: str, results) -> None:
        if query == self._pending_query:
            self._watchlist.show_search_results(results)

    def _on_symbol_submitted(self, text: str) -> None:
        """Free text typed into the search box: confirm it before adopting it."""
        symbol = text.strip().upper()
        if not symbol:
            return
        if self._watchlist.contains(symbol):
            self._watchlist.clear_search()
            self.set_active_symbol(symbol)
            return
        self._status(f"Looking up {symbol}…", 0)
        self._pool.start(_ValidateTask(self.market, symbol, self._validate_signals))

    def _on_validate_done(self, symbol: str, quote, error: str) -> None:
        if quote is None:
            self._status(f"⚠ {error or f'{symbol} is not a tradable symbol.'}",
                         6000, error=True)
            return
        self.statusBar().clearMessage()
        self._watchlist.add_symbol(quote.symbol)

    # ================================================================== #
    # Session actions
    # ================================================================== #
    def _new_session(self) -> None:
        dlg = NewSessionDialog(self)
        if dlg.exec():
            self._save(silent=True)
            session = dlg.result_session()
            try:
                self._store.save_session(session)
                self._store.set_setting("last_session_id", session.id)
            except StoreError as exc:
                QMessageBox.warning(self, "New Session", str(exc))
                return
            self._bind_session(session)

    def _open_session(self) -> None:
        dlg = OpenSessionDialog(self._store, self.session.id, self)
        if dlg.exec() and dlg.selected_id():
            self._save(silent=True)
            try:
                session = self._store.load_session(dlg.selected_id())
                self._store.set_setting("last_session_id", session.id)
            except StoreError as exc:
                QMessageBox.warning(self, "Open Session", str(exc))
                return
            self._bind_session(session)

    def _rename_session(self) -> None:
        name, ok = QInputDialog.getText(self, "Rename Session", "Session name:",
                                        text=self.session.name)
        if ok and name.strip():
            self.session.name = name.strip()
            self.setWindowTitle(f"{APP_NAME} (classic) — {self.session.name}")
            self._refresh_portfolio()
            self._save(silent=True)

    def _reset_session(self) -> None:
        if QMessageBox.question(
            self, "Reset Local Session",
            "Reset the local simulator's cash, positions, options, orders and "
            "history? (This does not affect your Alpaca account.)"
        ) != QMessageBox.StandardButton.Yes:
            return
        s = self.session
        s.cash = s.starting_balance
        s.realized_pl = 0.0
        s.positions.clear()
        s.option_positions.clear()
        s.trades.clear()
        s.pending_orders.clear()
        s.equity_curve.clear()
        self._selected_contract = None
        self._option_ticket.clear_contract()
        self._refresh_portfolio()
        self._refresh_history()
        self._save(silent=True)
        self._status("Local session reset.", 4000)

    def _show_analytics(self) -> None:
        session = self.broker.analytics_session() or self.session
        AnalyticsDialog(session, self).exec()

    # ================================================================== #
    # Theme / data source / broker
    # ================================================================== #
    def _toggle_theme(self) -> None:
        self._set_theme("light" if self._theme_name == "dark" else "dark")

    def _set_theme(self, name: str) -> None:
        self._theme_name = name
        theme.apply_theme(QApplication.instance(), name, legacy=True)
        self._chart.apply_theme()
        if self._active_quote is not None:
            self._price_header.update_quote(self._active_quote)
        self._refresh_portfolio()
        self._refresh_history()
        self._update_source_label()
        try:
            self._store.set_setting("theme", name)
        except StoreError:
            pass

    def _set_data_source(self, source: str) -> None:
        if source == self._data_source:
            return
        if source == "alpaca" and load_alpaca() is None:
            self._require_keys("Alpaca market data")
            self._sync_menu_checks()
            return
        self._data_source = source
        self._stop_feed()
        self.market = self._make_market(source)
        self._start_feed()
        self._sync_feed_symbols(force=True)
        if self._feed is not None:
            self._feed.set_active_symbol(self._active_symbol)
            self._feed.request_immediate()
        self._update_source_label()
        self._store.set_setting("data_source", self._data_source)
        self._sync_menu_checks()

    def _set_broker_mode(self, mode: str) -> None:
        if mode == self._broker_mode:
            return
        if mode == "alpaca" and load_alpaca() is None:
            self._require_keys("Alpaca trading")
            self._sync_menu_checks()
            return
        self._stop_broker_feed()
        self._broker_mode = mode
        self.broker = self._make_broker(mode)
        if isinstance(self.broker, LocalBroker):
            self.broker.bind(self.session)
        self._start_broker_feed()
        self._refresh_portfolio()
        self._refresh_history()
        self._update_source_label()
        self._store.set_setting("broker", self._broker_mode)
        self._sync_menu_checks()
        self._sync_options_availability()
        self._status(f"Trading account: {self.broker.display_name}.", 4000)

    def _edit_keys(self) -> None:
        dlg = AlpacaKeysDialog(self)
        if not dlg.exec():
            return
        reconnected = False
        if self._broker_mode == "alpaca":
            self._stop_broker_feed()
            self.broker = self._make_broker("alpaca")
            self._start_broker_feed()
            reconnected = True
        if self._data_source == "alpaca":
            self._stop_feed()
            self.market = self._make_market("alpaca")
            self._start_feed()
            self._sync_feed_symbols(force=True)
            if self._feed is not None:
                self._feed.set_active_symbol(self._active_symbol)
                self._feed.request_immediate()
            reconnected = True
        self._refresh_portfolio()
        self._refresh_history()
        self._update_source_label()
        self._status(
            "Alpaca keys saved and reconnected." if reconnected
            else "Alpaca keys saved. Switch Account → Alpaca paper account to connect.",
            5000)

    def _require_keys(self, what: str) -> None:
        QMessageBox.information(
            self, "Alpaca keys needed",
            f"{what} needs your Alpaca API keys. Enter them in "
            "Account → Alpaca API Keys.")

    def _sync_menu_checks(self) -> None:
        for act in self._broker_group.actions():
            act.setChecked((act.text().startswith("Alpaca")) == (self._broker_mode == "alpaca"))
        for act in self._source_group.actions():
            act.setChecked(self._data_source in act.text().lower())

    def _update_source_label(self) -> None:
        broker_txt = "Alpaca paper" if self.broker.is_remote else "Local sim"
        src = {"alpaca": "Alpaca IEX", "yahoo": "Yahoo", "demo": "Demo"}.get(
            self._data_source, self._data_source)
        color = theme.gain_color() if self.broker.is_remote else theme.color("accent")
        self._source_label.setText(f"● {broker_txt}  ·  data: {src}")
        self._source_label.setStyleSheet(
            f"color: {color}; padding: 0 8px; font-weight: 600;")

    # ================================================================== #
    # Persistence
    # ================================================================== #
    def _save(self, silent: bool = True) -> None:
        try:
            self._store.save_session(self.session)
            if not silent:
                self._status("Saved.", 2500)
        except StoreError as exc:
            self._status(f"⚠ Save failed: {exc}", 6000, error=True)

    # ================================================================== #
    def closeEvent(self, event) -> None:
        self._stop_feed()
        self._stop_broker_feed()
        self._save(silent=True)
        try:
            self._store.set_setting("last_session_id", self.session.id)
            self._store.set_setting("theme", self._theme_name)
            self._store.set_setting("data_source", self._data_source)
            self._store.set_setting("broker", self._broker_mode)
        except StoreError:
            pass
        super().closeEvent(event)


# --------------------------------------------------------------------------- #
def _mode_segment(text: str, checked: bool = False) -> QPushButton:
    """A pill button for the Stock / Options market-mode toggle."""
    b = QPushButton(text)
    b.setObjectName("Segment")
    b.setCheckable(True)
    b.setChecked(checked)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _panel(inner: QWidget, margin: int = 14) -> QFrame:
    frame = QFrame()
    frame.setObjectName("Panel")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(margin, margin, margin, margin)
    lay.addWidget(inner)
    return frame


def _add(menu, text: str, slot, shortcut: str | None = None) -> QAction:
    action = QAction(text, menu)
    action.setMenuRole(QAction.MenuRole.NoRole)
    if shortcut:
        action.setShortcut(shortcut)
    action.triggered.connect(lambda _=False: slot())
    menu.addAction(action)
    return action
