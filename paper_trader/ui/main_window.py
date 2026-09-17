"""The main window — composition root of the UI.

It owns the local session (watchlist + offline-sim state), the active **broker**
(the local simulator or a live Alpaca paper account), the market-data service and
two background threads: the market-data feed (prices/charts) and, for remote
brokers, an account poller. Widgets stay dumb views; MainWindow is the only place
that talks to the broker, reconfigures the feeds, or persists.

    market feed  ─ quote/chart signals ─▶ price header · chart · watchlist
    broker poll  ─ updated signal ──────▶ portfolio strip · positions · orders
    widget signals (trade/select/search) ─▶ MainWindow ─▶ broker / feed / store

The page is laid out like a broker's instrument page: nav bar, account strip,
then watchlist rail | price + chart | order card, with the blotter tabs beneath.
Anything that touches the network off a user action (symbol validation, orders
against a remote broker) is dispatched to the thread pool so the window never
blocks on HTTP.
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
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
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
from . import anim, theme
from .controllers.broker_feed import BrokerFeed
from .controllers.data_feed import DataFeed
from .dialogs import AlpacaKeysDialog, AnalyticsDialog, NewSessionDialog, OpenSessionDialog
from .nav_bar import NavBar
from .widgets.chart import ChartWidget
from .widgets.history_table import HistoryTable, OrdersTable
from .widgets.option_ticket import OptionOrderTicket, OptionTicket
from .widgets.options_chain import OptionsChainView
from .widgets.options_positions import OptionsPositionsTable
from .widgets.portfolio_bar import PortfolioBar
from .widgets.positions_table import PositionsTable
from .widgets.price_header import DayStatsCard, PriceHeader
from .widgets.trade_panel import OrderTicket, TradePanel
from .widgets.watchlist import WatchlistPanel
from .format import fmt_shares


# --------------------------------------------------------------------------- #
# Off-thread helpers
# --------------------------------------------------------------------------- #
class _SearchSignals(QObject):
    done = pyqtSignal(str, object)          # query, list[SearchResult]


class _ValidateSignals(QObject):
    done = pyqtSignal(str, object, str)     # symbol, Quote|None, error


class _OrderSignals(QObject):
    done = pyqtSignal(int, object, str)     # token, message|None, error


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
    """Confirm a typed symbol exists before it joins the watchlist."""

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
        except Exception as exc:  # never let a pool thread die on bad input
            self._signals.done.emit(self._symbol, None, f"Lookup failed: {exc}")
        else:
            self._signals.done.emit(self._symbol, quote, "")


class _OrderTask(QRunnable):
    """Run one broker call off the GUI thread (remote brokers are HTTP)."""

    def __init__(self, fn, token: int, signals: _OrderSignals) -> None:
        super().__init__()
        self._fn = fn
        self._token = token
        self._signals = signals

    def run(self) -> None:
        try:
            message = self._fn()
        except BrokerError as exc:
            self._signals.done.emit(self._token, None, str(exc))
        except Exception as exc:
            self._signals.done.emit(self._token, None, f"Unexpected error: {exc}")
        else:
            self._signals.done.emit(self._token, message, "")


# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self, session: Session, store: Store, broker_mode: str = "local",
                 data_source: str = "demo", theme_name: str = "dark") -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1560, 980)
        # The floor is what the page actually needs: hero + chart + day stats in
        # the centre column, and the blotter's tab bar plus a row beneath it.
        # Allowing less than this is how widgets end up overlapping.
        self.setMinimumSize(1160, 820)

        self._store = store
        self.session = session
        self._theme_name = theme_name
        self._broker_mode = broker_mode
        self._data_source = data_source

        # Chart chrome lives in the Theme menu and persists between runs. The
        # defaults are the bare Robinhood line; the candle view always keeps its
        # scales regardless (see ChartWidget._sync_axis_visibility).
        settings = store.load_settings()
        self._chrome = {
            "axes": bool(settings.get("chart_axes", False)),
            "grid": bool(settings.get("chart_grid", False)),
            "last_price": bool(settings.get("chart_last_price", True)),
        }
        anim.ENABLED = bool(settings.get("animations", True))

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
        # A status message the user must not lose to the next price tick.
        self._status_is_error = False

        # Off-thread plumbing.
        self._search_signals = _SearchSignals()
        self._search_signals.done.connect(self._on_search_done)
        self._validate_signals = _ValidateSignals()
        self._validate_signals.done.connect(self._on_validate_done)
        self._order_signals = _OrderSignals()
        self._order_signals.done.connect(self._on_order_done)
        self._pending_query = ""
        self._order_token = 0
        self._order_in_flight = False
        self._pool = QThreadPool.globalInstance()

        # Thread handles.
        self._thread: QThread | None = None
        self._feed: DataFeed | None = None
        self._broker_thread: QThread | None = None
        self._broker_feed: BrokerFeed | None = None

        self._build_menu()
        self._build_ui()
        self._chart.set_chrome(**self._chrome)
        self._install_nav_controls()
        self._bind_session(session, initial=True)
        self._sync_options_availability()
        self._start_feed()
        self._start_broker_feed()

        self._sized = False

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
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._nav = NavBar(APP_NAME)
        self._nav.searchRequested.connect(self._on_search_requested)
        self._nav.symbolChosen.connect(self._on_symbol_chosen)
        self._nav.symbolSubmitted.connect(self._on_symbol_submitted)
        outer.addWidget(self._nav)

        self._portfolio_bar = PortfolioBar()
        outer.addWidget(self._portfolio_bar)

        body = QWidget()
        body_box = QVBoxLayout(body)
        body_box.setContentsMargins(22, 18, 22, 10)
        body_box.setSpacing(14)

        # ---- centre column: price hero, mode toggle, chart, day stats ----- #
        self._price_header = PriceHeader()
        self._chart = ChartWidget()
        self._chart.rangeChanged.connect(self._on_range_changed)
        self._options_chain = OptionsChainView()
        self._options_chain.contractSelected.connect(self._on_contract_selected)
        self._day_stats = DayStatsCard()

        center = QWidget()
        center_box = QVBoxLayout(center)
        center_box.setContentsMargins(8, 0, 8, 0)
        center_box.setSpacing(12)
        center_box.addWidget(self._price_header)
        center_box.addLayout(self._build_mode_toggle())

        # Chart (stock) / chain (options) swap in the centre.
        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(self._chart)           # 0: stock
        self._center_stack.addWidget(self._options_chain)   # 1: options
        center_box.addWidget(self._center_stack, 1)
        center_box.addWidget(self._day_stats)

        # ---- left rail: watchlist ----------------------------------------- #
        self._watchlist = WatchlistPanel()
        self._watchlist.symbolSelected.connect(self.set_active_symbol)
        self._watchlist.watchlistChanged.connect(self._on_watchlist_changed)
        rail = QFrame()
        rail.setObjectName("Panel")
        rail_box = QVBoxLayout(rail)
        rail_box.setContentsMargins(0, 0, 8, 0)
        rail_box.addWidget(self._watchlist)

        # ---- right column: order card + secondary actions ----------------- #
        self._trade_panel = TradePanel()
        self._trade_panel.orderRequested.connect(self._on_order_requested)
        self._option_ticket = OptionTicket()
        self._option_ticket.orderRequested.connect(self._on_option_order_requested)

        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(_card(self._trade_panel))    # 0: stock
        self._right_stack.addWidget(_card(self._option_ticket))  # 1: options

        right_inner = QWidget()
        right_box = QVBoxLayout(right_inner)
        right_box.setContentsMargins(8, 0, 8, 0)
        right_box.setSpacing(12)
        right_box.addWidget(self._right_stack)
        self._options_cta = QPushButton("Trade Options")
        self._options_cta.setObjectName("Outline")
        self._options_cta.setCursor(Qt.CursorShape.PointingHandCursor)
        self._options_cta.clicked.connect(self._toggle_market_mode)
        self._watch_cta = QPushButton("Watch")
        self._watch_cta.setObjectName("Outline")
        self._watch_cta.setCursor(Qt.CursorShape.PointingHandCursor)
        self._watch_cta.clicked.connect(self._toggle_watch)
        right_box.addWidget(self._options_cta)
        right_box.addWidget(self._watch_cta)
        right_box.addStretch(1)

        right = QScrollArea()
        right.setWidget(right_inner)
        right.setWidgetResizable(True)
        right.setFrameShape(QFrame.Shape.NoFrame)
        right.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right.viewport().setStyleSheet("background: transparent;")

        # The three columns are draggable: the rail and the card have sensible
        # minimums (and the card a maximum), and the chart absorbs the slack.
        rail.setMinimumWidth(180)
        rail.setMaximumWidth(420)
        center.setMinimumWidth(420)
        right.setMinimumWidth(300)
        right.setMaximumWidth(460)
        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(rail)
        top.addWidget(center)
        top.addWidget(right)
        top.setStretchFactor(0, 0)
        top.setStretchFactor(1, 1)
        top.setStretchFactor(2, 0)
        top.setSizes([250, 900, 330])
        top.setChildrenCollapsible(False)
        top.setHandleWidth(9)
        self._top_split = top

        # ---- blotter tabs -------------------------------------------------- #
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

        # Dragging the handle must always leave the tab bar and a row of the
        # table on screen — a minimum, not a collapse.
        self._tabs.setMinimumHeight(132)
        main_split = QSplitter(Qt.Orientation.Vertical)
        main_split.addWidget(top)
        main_split.addWidget(self._tabs)
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 0)
        main_split.setSizes([1000, 250])  # ~4:1 — the chart gets the lion's share
        main_split.setChildrenCollapsible(False)
        main_split.setHandleWidth(9)
        self._main_split = main_split
        body_box.addWidget(main_split, 1)

        outer.addWidget(body, 1)
        self.setCentralWidget(central)
        # Create the status bar up front: adding it lazily on the first message
        # would resize everything above it mid-session.
        self.statusBar().setSizeGripEnabled(False)
        self._update_source_label()

    def _install_nav_controls(self) -> None:
        """Mirror the menu actions into the nav bar (the app's primary chrome)."""
        acct_menu = QMenu(self._nav)
        acct_menu.addActions(self._broker_group.actions())
        acct_menu.addSeparator()
        acct_menu.addAction("Alpaca API Keys…", self._edit_keys)
        self._nav.add_menu("Account", acct_menu)

        data_menu = QMenu(self._nav)
        data_menu.addActions(self._source_group.actions())
        self._nav.add_menu("Market Data", data_menu)

        self._nav.add_action("Analytics", self._show_analytics)
        appearance = self._appearance_menu()
        self._nav.add_menu("Theme", appearance)
        # The menu bar gets the same switches, so they're reachable either way.
        self._view_chart_menu.addActions(list(self._chrome_actions.values()))
        self._view_chart_menu.addSeparator()
        self._view_chart_menu.addAction(self._motion_action)

    def _appearance_menu(self) -> QMenu:
        """Dark/light plus the switches for how much chart furniture to draw."""
        menu = QMenu("Theme", self)

        self._theme_group = QActionGroup(self)
        for label, name in (("Dark", "dark"), ("Light", "light")):
            act = QAction(label, self, checkable=True)
            act.setMenuRole(QAction.MenuRole.NoRole)
            act.setChecked(name == self._theme_name)
            act.triggered.connect(lambda _=False, n=name: self._set_theme(n))
            self._theme_group.addAction(act)
            menu.addAction(act)

        menu.addSeparator()
        heading = QAction("Chart", self)
        heading.setEnabled(False)
        menu.addAction(heading)

        self._chrome_actions: dict[str, QAction] = {}
        for key, label, tip in (
            ("axes", "Price && time axes",
             "Off keeps the line view bare — the candlestick view always shows "
             "its price and time scales."),
            ("grid", "Gridlines", "Faint horizontal rules behind the price."),
            ("last_price", "Last-price tag",
             "A tag pinned to the right edge with the latest price."),
        ):
            act = QAction(label, self, checkable=True)
            act.setMenuRole(QAction.MenuRole.NoRole)
            act.setToolTip(tip)
            act.setChecked(self._chrome[key])
            act.toggled.connect(lambda on, k=key: self._set_chart_chrome(k, on))
            menu.addAction(act)
            self._chrome_actions[key] = act

        menu.addSeparator()
        motion = QAction("Animations", self, checkable=True)
        motion.setMenuRole(QAction.MenuRole.NoRole)
        motion.setToolTip("Rolling numbers, price flashes and the chart's draw-in.")
        motion.setChecked(anim.ENABLED)
        motion.toggled.connect(self._set_animations)
        menu.addAction(motion)
        self._motion_action = motion
        return menu

    def _set_chart_chrome(self, key: str, enabled: bool) -> None:
        self._chrome[key] = enabled
        self._chart.set_chrome(**{key: enabled})
        try:
            self._store.set_setting(f"chart_{key}", enabled)
        except StoreError:
            pass

    def _set_animations(self, enabled: bool) -> None:
        anim.ENABLED = enabled
        try:
            self._store.set_setting("animations", enabled)
        except StoreError:
            pass

    def _build_mode_toggle(self) -> QHBoxLayout:
        """The Stock / Options segmented control above the centre stack."""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(0, 2, 0, 0)
        self._mode_group = QButtonGroup(self)
        self._stock_mode_btn = _mode_segment("Stock", checked=True)
        self._options_mode_btn = _mode_segment("Options")
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
    def _toggle_market_mode(self) -> None:
        self._set_market_mode("stock" if self._market_mode == "options" else "options")

    def _set_market_mode(self, mode: str) -> None:
        if mode == "options" and not self.broker.supports_options:
            # Options run on the local simulator. Rather than leaving the button
            # inert, offer to switch the trading account to it in one click.
            resp = QMessageBox.question(
                self, "Trade options",
                "Options trading runs on the Local simulator. Switch your "
                "trading account to it now?\n\nYour Alpaca account is untouched — "
                "switch back anytime from Account → Trading Account.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if resp != QMessageBox.StandardButton.Yes:
                self._sync_mode_buttons("stock")
                return
            self._set_broker_mode("local")
            if not self.broker.supports_options:  # switch didn't take — bail safely
                self._sync_mode_buttons("stock")
                return
            self._status("Switched to the Local simulator to trade options.", 5000)
        self._market_mode = mode
        is_opt = mode == "options"
        anim.cross_fade_stack(self._center_stack, 1 if is_opt else 0)
        anim.cross_fade_stack(self._right_stack, 1 if is_opt else 0)
        self._sync_mode_buttons(mode)
        if is_opt:
            self._options_chain.set_underlying(
                self._active_symbol, self._prices.get(self._active_symbol))
            self._refresh_option_ticket()

    def _sync_mode_buttons(self, mode: str) -> None:
        is_opt = mode == "options"
        self._stock_mode_btn.setChecked(not is_opt)
        self._options_mode_btn.setChecked(is_opt)
        sym = self._active_symbol
        self._options_cta.setText(
            f"Back to {sym} Chart" if is_opt else f"Trade {sym} Options".strip())

    def _sync_options_availability(self) -> None:
        # Extended-hours (pre/after-market) trading is an Alpaca capability; the
        # trade panel shows its opt-in only for a remote (Alpaca) account.
        self._trade_panel.set_extended_hours_supported(self.broker.is_remote)
        # The Options button is always clickable; on an account that can't trade
        # options, clicking it offers to switch to the local simulator (see
        # _set_market_mode). If the account changed to a non-options one while we
        # were showing options, fall back to the stock view.
        ok = self.broker.supports_options
        tip = "" if ok else "Options trade on the Local simulator — click to switch."
        self._options_mode_btn.setToolTip(tip)
        self._options_cta.setToolTip(tip)
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
    # Menus
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
        _add(view_menu, "&Find Symbol", lambda: self._nav.focus_search(),
             QKeySequence.StandardKey.Find)
        view_menu.addSeparator()
        self._view_chart_menu = view_menu.addMenu("&Chart Display")
        view_menu.addSeparator()
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
        self._day_stats.clear()
        self._chart.set_symbol(first)
        self._trade_panel.set_symbol(first)
        self._watchlist.set_active(first)
        self._selected_contract = None
        self._option_ticket.clear_contract()
        self._options_chain.set_underlying(first, None)
        self._sync_symbol_actions()

        self._refresh_portfolio()
        self._refresh_history()
        self.setWindowTitle(f"{APP_NAME} — {session.name}")
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
            # Long enough for an in-flight HTTP call to unwind, short enough that
            # quitting never feels stuck.
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
        self._day_stats.clear()
        self._chart.set_symbol(symbol)
        self._trade_panel.set_symbol(symbol)
        self._trade_panel.set_market_price(self._prices.get(symbol))
        self._watchlist.set_active(symbol)
        # Options chain follows the active underlying; drop any stale selection.
        self._options_chain.set_underlying(symbol, self._prices.get(symbol))
        if self._selected_contract is not None and self._selected_contract.underlying != symbol:
            self._selected_contract = None
            self._option_ticket.clear_contract()
        self._sync_symbol_actions()
        self._tabs.setCurrentIndex(0)
        self._refresh_portfolio()
        if self._feed is not None:
            self._feed.set_active_symbol(symbol)
            self._feed.set_range(self._chart.current_range())
            self._feed.request_immediate()

    def _sync_symbol_actions(self) -> None:
        """Keep the secondary card buttons in step with the active symbol."""
        sym = self._active_symbol
        self._sync_mode_buttons(self._market_mode)
        watched = self._watchlist.contains(sym)
        self._watch_cta.setText(f"Unwatch {sym}" if watched else f"Watch {sym}")

    def _toggle_watch(self) -> None:
        sym = self._active_symbol
        if not sym:
            return
        if self._watchlist.contains(sym):
            self._watchlist.remove_symbol(sym)
            self._status(f"{sym} removed from your watchlist.", 4000)
        else:
            self._watchlist.add_symbol(sym, select=False)
            self._status(f"{sym} added to your watchlist.", 4000)
        self._sync_symbol_actions()

    # ================================================================== #
    # Market-feed slots
    # ================================================================== #
    def _on_quote(self, quote) -> None:
        self._prices[quote.symbol] = quote.price
        self._prev_closes[quote.symbol] = quote.previous_close
        if quote.symbol == self._active_symbol:
            self._active_quote = quote
            self._price_header.update_quote(quote)
            self._day_stats.update_quote(quote)
            self._chart.update_reference(quote.previous_close)
            self._trade_panel.set_market_price(quote.price)
            self._trade_panel.set_session(quote.market_state)
            self._options_chain.set_price(quote.price)
        self._watchlist.update_quotes({quote.symbol: quote})
        # Data is flowing again: retire a stale error *before* this tick's own
        # fills get a chance to post their messages.
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
        # Local limit orders fill on price ticks; remote brokers do this server-side.
        for msg in self.broker.on_price_tick(self._prices):
            self._status(msg, 6000)
            self._refresh_history()
            saved = True
        # Expired option contracts settle (exercise/assignment) once we have a price.
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
        """Drop a stale error, but never a fill/settlement notice."""
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
        # Underlyings of open option positions must be priced for valuation/settlement.
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
    def _run_order(self, call, *, on_success=None) -> None:
        """Execute a broker call.

        The local simulator is pure computation, so it runs inline and the UI
        updates in the same frame. A remote broker means HTTP — timeouts, retries
        and rate-limit waits — so that goes to the thread pool and the ticket is
        disabled until it comes back, rather than freezing the window.
        """
        if not self.broker.is_remote:
            try:
                message = call()
            except BrokerError as exc:
                QMessageBox.warning(self, "Order rejected", str(exc))
                return
            except Exception as exc:
                QMessageBox.critical(self, "Order error", f"Unexpected error: {exc}")
                return
            self._finish_order(message, on_success)
            return

        if self._order_in_flight:
            self._status("An order is already being submitted…", 3000)
            return
        self._order_token += 1
        self._order_in_flight = True
        self._on_success = on_success
        self._set_tickets_busy(True)
        self._status("Submitting order…", 0)
        self._pool.start(_OrderTask(call, self._order_token, self._order_signals))

    def _on_order_done(self, token: int, message, error: str) -> None:
        if token != self._order_token:
            return  # a stale reply (the account was switched mid-flight)
        self._order_in_flight = False
        self._set_tickets_busy(False)
        if error:
            self.statusBar().clearMessage()
            QMessageBox.warning(self, "Order rejected", error)
            return
        self._finish_order(message, getattr(self, "_on_success", None))

    def _finish_order(self, message, on_success) -> None:
        if message:
            self._status(message, 6000)
        if on_success is not None:
            on_success()
        self._after_trade()

    def _set_tickets_busy(self, busy: bool) -> None:
        self._trade_panel.set_busy(busy)
        self._option_ticket.set_busy(busy)

    def _on_order_requested(self, ticket: OrderTicket) -> None:
        symbol = self._active_symbol
        if not symbol:
            return
        price = self._prices.get(symbol)
        if ticket.order_type == "LIMIT":
            if not ticket.limit_price or ticket.limit_price <= 0:
                QMessageBox.warning(self, "Order rejected", "Enter a valid limit price.")
                return
            qty = ticket.value / ticket.limit_price if ticket.mode == "DOLLARS" else ticket.value
            side = Side.BUY if ticket.side == "BUY" else Side.SELL
            call = lambda: self.broker.place_limit(  # noqa: E731
                symbol, side, qty, ticket.limit_price,
                extended_hours=ticket.extended_hours)
        elif ticket.side == "BUY":
            call = lambda: self.broker.buy_market(  # noqa: E731
                symbol, price,
                quantity=None if ticket.mode == "DOLLARS" else ticket.value,
                notional=ticket.value if ticket.mode == "DOLLARS" else None)
        else:
            call = lambda: self.broker.sell_market(  # noqa: E731
                symbol, price,
                quantity=None if ticket.mode == "DOLLARS" else ticket.value,
                notional=ticket.value if ticket.mode == "DOLLARS" else None)
        self._run_order(call, on_success=self._trade_panel.clear_amount)

    def _sell_all(self, symbol: str) -> None:
        owned = self.broker.position_quantity(symbol)
        if owned <= 0:
            return
        if QMessageBox.question(
            self, "Sell all",
            f"Sell your entire position of {fmt_shares(owned)} {symbol}?"
        ) != QMessageBox.StandardButton.Yes:
            return
        price = self._prices.get(symbol)
        self._run_order(lambda: self.broker.sell_market(symbol, price, sell_all=True))

    def _on_option_order_requested(self, ticket: OptionOrderTicket) -> None:
        if ticket.side == "BUY":
            call = lambda: self.broker.buy_option(  # noqa: E731
                ticket.contract, ticket.quantity, ticket.price, self._prices)
        else:
            call = lambda: self.broker.sell_option(  # noqa: E731
                ticket.contract, ticket.quantity, ticket.price, self._prices)
        self._run_order(call, on_success=self._refresh_option_ticket)

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
        if qty > 0:   # long → sell to close at the bid
            call = lambda: self.broker.sell_option(  # noqa: E731
                contract, abs(qty), quote.bid, self._prices)
        else:         # short → buy to close at the ask
            call = lambda: self.broker.buy_option(  # noqa: E731
                contract, abs(qty), quote.ask, self._prices)
        self._run_order(call, on_success=self._refresh_option_ticket)

    def _cancel_order(self, order_id: str) -> None:
        self._run_order(lambda: self._cancel(order_id))

    def _cancel(self, order_id: str) -> str:
        self.broker.cancel_order(order_id)
        return "Order cancelled."

    def _after_trade(self) -> None:
        if not self.broker.is_remote:
            # Keep the saved order list from growing without bound.
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
        self._sync_symbol_actions()
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
            self._nav.show_results(results)

    def _on_symbol_chosen(self, symbol: str) -> None:
        """A provider-supplied result — it exists, so add it straight away."""
        self._watchlist.add_symbol(symbol)
        self.set_active_symbol(symbol)
        self._sync_symbol_actions()

    def _on_symbol_submitted(self, text: str) -> None:
        """Free text typed into the search box: confirm it before adopting it.

        Unvalidated input used to land in the watchlist verbatim, where it would
        fail on every subsequent poll and sit there forever.
        """
        symbol = text.strip().upper()
        if not symbol:
            return
        if self._watchlist.contains(symbol):
            self._nav.clear_search()
            self.set_active_symbol(symbol)
            return
        self._status(f"Looking up {symbol}…", 0)
        self._pool.start(_ValidateTask(self.market, symbol, self._validate_signals))

    def _on_validate_done(self, symbol: str, quote, error: str) -> None:
        if quote is None:
            self._status(f"⚠ {error or f'{symbol} is not a tradable symbol.'}",
                         6000, error=True)
            return
        self._nav.clear_search()
        self.statusBar().clearMessage()
        self._watchlist.add_symbol(quote.symbol)
        self.set_active_symbol(quote.symbol)
        self._sync_symbol_actions()

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
            self.setWindowTitle(f"{APP_NAME} — {self.session.name}")
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
        # Option positions are part of the account too — leaving them behind used
        # to strand contracts with no trade history and eat the restored cash as
        # collateral.
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
        theme.apply_theme(QApplication.instance(), name)
        for act in getattr(self, "_theme_group", QActionGroup(self)).actions():
            act.setChecked(act.text().lower() == name)
        self._chart.apply_theme()
        self._nav.refresh_theme()
        if self._active_quote is not None:
            self._price_header.update_quote(self._active_quote)
            self._day_stats.update_quote(self._active_quote)
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
        # Invalidate any order still in flight against the previous account.
        self._order_token += 1
        self._order_in_flight = False
        self._set_tickets_busy(False)
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
        # Rebuild any live Alpaca connections so the new keys take effect now
        # (the client caches the key, so we recreate broker/market as needed).
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
        color = theme.gain_color() if self.broker.is_remote else theme.color("text_muted")
        self._nav.set_status(f"● {broker_txt}  ·  {src}", color)

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
    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Split the body the first time we know how tall it actually is.

        Sizes set during construction are scaled against a not-yet-laid-out
        splitter, which left the chart at its floor while the blotter took the
        slack. Applying the ratio on first show gets it right; after that the
        user's own drags (and the stretch factors) take over.
        """
        super().showEvent(event)
        if not self._sized:
            self._sized = True
            total = self._main_split.height()
            if total > 400:
                self._main_split.setSizes([int(total * 0.84), int(total * 0.16)])

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


def _card(inner: QWidget, margin: int = 20) -> QFrame:
    """Wrap a widget in the lifted, outlined card used down the right column."""
    frame = QFrame()
    frame.setObjectName("Card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(margin, margin, margin, margin)
    lay.addWidget(inner)
    return frame


def _add(menu, text: str, slot, shortcut=None) -> QAction:
    action = QAction(text, menu)
    # Keep items where we put them — macOS otherwise relocates some by text.
    action.setMenuRole(QAction.MenuRole.NoRole)
    if shortcut:
        action.setShortcut(shortcut)
    action.triggered.connect(lambda _=False: slot())
    menu.addAction(action)
    return action
