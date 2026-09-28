"""UI-level regression tests (offscreen, no network).

Plain-assert style so it runs with no test framework:

    QT_QPA_PLATFORM=offscreen python tests/test_ui.py

Covers the behaviours that are easy to break by accident in the view layer: the
chart's zoom contract, empty-state rendering, theme switching, the status line,
and the session-reset invariants. Everything runs against the synthetic data
provider and the local simulator, so it never touches the network.
"""

import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("PAPER_TRADER_HOME", tempfile.mkdtemp(prefix="pt_uitest_"))

import numpy as np
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication([])
app.setStyle("Fusion")

from paper_trader.core.models import Order, OrderStatus, Session, Side  # noqa: E402
from paper_trader.core.options import OptionContract, OptionRight  # noqa: E402
from paper_trader.data.models import Quote  # noqa: E402
from paper_trader.data.market_data import SyntheticProvider  # noqa: E402
from paper_trader.persistence.store import Store  # noqa: E402
from paper_trader.ui import anim, theme  # noqa: E402
from paper_trader.ui.format import fmt_money, fmt_price, fmt_signed_money, fmt_signed_pct  # noqa: E402
from paper_trader.ui.main_window import MainWindow  # noqa: E402
from paper_trader.ui.widgets.chart import ChartWidget  # noqa: E402
from paper_trader.ui.widgets.history_table import OrdersTable  # noqa: E402
from paper_trader.ui.widgets.options_positions import OptionsPositionsTable  # noqa: E402
from paper_trader.ui.widgets.positions_table import PositionsTable  # noqa: E402

anim.ENABLED = False  # deterministic: no tweens mid-assertion (see _window)

_checks: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    _checks.append((name, bool(cond)))


def _window() -> MainWindow:
    theme.apply_theme(app, "dark")
    store = Store()
    # The window applies the saved animation preference, so express "no tweens
    # mid-assertion" the way a user would rather than poking the module flag.
    store.set_setting("animations", False)
    session = Session.new("UI Test", 10_000.0, ["AAPL", "MSFT"])
    store.save_session(session)
    win = MainWindow(session, store, broker_mode="local", data_source="demo",
                     theme_name="dark")
    win.resize(1500, 950)
    win.show()
    app.processEvents()
    return win


def _quote(symbol: str, price: float, prev: float | None = None) -> Quote:
    return Quote(symbol=symbol, price=price, previous_close=prev or price,
                 market_state="REGULAR", timestamp=datetime.now(timezone.utc))


# --------------------------------------------------------------------------- #
def test_chart_zoom_refits_price_axis() -> None:
    """Zooming the time axis must magnify the bars, not flatten them."""
    quote, candles = SyntheticProvider().fetch_chart("AAPL", "1D")
    for mode in ("line", "candles"):
        w = ChartWidget()
        w.resize(1000, 480)
        w.show()
        w._mode = mode
        w._sync_axis_visibility()
        w.set_symbol("AAPL")
        w.update_reference(quote.previous_close)
        w.set_candles("AAPL", "1D", candles)
        app.processEvents()
        vb = w._plot.getViewBox()
        (x0, x1), (y0, y1) = vb.viewRange()
        full_span = y1 - y0

        # Zoom around the middle of the *data*: a live 1D chart frames the whole
        # trading day, so the middle of the view can be an empty afternoon.
        first, last = candles[0].epoch, candles[-1].epoch
        mid = (first + last) / 2
        half = (last - first) * 0.05
        vb.setXRange(mid - half, mid + half, padding=0)
        app.processEvents()
        (zx0, zx1), (zy0, zy1) = vb.viewRange()
        visible = [c for c in candles if zx0 <= c.epoch <= zx1]
        lo = min((c.low if mode == "candles" else c.close) for c in visible)
        hi = max((c.high if mode == "candles" else c.close) for c in visible)

        check(f"{mode}: zoom shrinks the price range", (zy1 - zy0) < full_span * 0.9)
        check(f"{mode}: visible bars stay in frame", zy0 <= lo and zy1 >= hi)
        check(f"{mode}: price padding stays tight",
              (zy1 - zy0) < (hi - lo) * 1.3 + 0.05)
        check(f"{mode}: reset affordance appears when zoomed",
              not w._reset_btn.isHidden())

        w.reset_zoom()
        app.processEvents()
        (rx0, rx1), _ = vb.viewRange()
        check(f"{mode}: reset restores the full series",
              (rx1 - rx0) > (x1 - x0) * 0.95 and w._reset_btn.isHidden())


def test_chart_drops_the_previous_symbol() -> None:
    """Switching symbols must not leave the old line under the new name."""
    _q, candles = SyntheticProvider().fetch_chart("AAPL", "1D")
    w = ChartWidget()
    w.set_symbol("AAPL")
    w.set_candles("AAPL", "1D", candles)
    check("chart holds the series it was given", len(w._candles) == len(candles))
    w.set_symbol("MSFT")
    xs, _ys = w._line_item.getData()
    check("chart clears on symbol change", not w._candles and (xs is None or len(xs) == 0))


def test_tables_show_their_empty_state() -> None:
    """A brand-new account should explain itself, not show a bare header."""
    for table_cls, word in ((PositionsTable, "No open positions"),
                            (OptionsPositionsTable, "No option positions")):
        t = table_cls()
        t.update_positions([])
        item = t._table.item(0, 0)
        check(f"{table_cls.__name__} renders its empty state",
              t._table.rowCount() == 1 and item is not None and word in item.text())


def test_orders_table_separates_dollars_from_shares() -> None:
    """A notional order is a dollar amount, not a share count."""
    table = OrdersTable()
    order = Order(id="a1", created_at=datetime.now(timezone.utc), symbol="AAPL",
                  side=Side.BUY, quantity=0.0, limit_price=0.0,
                  status=OrderStatus.PENDING, notional=500.0)
    table.update_orders([order])
    check("notional order shows dollars", table._table.item(0, 3).text() == "$500.00")


def test_theme_switch_keeps_the_price_visible() -> None:
    """The flashing price label must adopt the new palette's text colour."""
    win = _window()
    win._on_quote(_quote("AAPL", 100.0, 99.0))
    win._on_quote(_quote("AAPL", 101.0, 99.0))   # a tick, so the flash runs
    app.processEvents()
    win._set_theme("light")
    app.processEvents()
    style = win._price_header._price_label.styleSheet().lower()
    check("price is not left white on the light theme", "#ffffff" not in style)
    check("price adopts the light text colour",
          theme.PALETTES["light"]["text"] in style)
    win._set_theme("dark")
    app.processEvents()
    style = win._price_header._price_label.styleSheet().lower()
    check("price adopts the dark text colour",
          theme.PALETTES["dark"]["text"] in style)
    win.close()


def test_fill_message_survives_the_next_tick() -> None:
    """A limit fill posts a status message; the same tick must not wipe it."""
    win = _window()
    engine = win.broker.engine
    engine.market_buy("AAPL", 100.0, quantity=5)
    engine.place_limit("AAPL", Side.SELL, 5, 105.0)
    win._on_quote(_quote("AAPL", 110.0, 100.0))   # crosses the limit
    app.processEvents()
    message = win.statusBar().currentMessage()
    check("limit-fill notice reaches the status bar", "Limit filled" in message)

    # An error, however, should be retired as soon as data flows again.
    win._on_feed_error("network", "Rate limited by data provider.")
    check("feed error is shown", "⚠" in win.statusBar().currentMessage())
    win._on_quote(_quote("AAPL", 111.0, 100.0))
    check("feed error clears once data returns", "⚠" not in win.statusBar().currentMessage())
    win.close()


def test_reset_clears_option_positions() -> None:
    """Reset means reset: no contracts may outlive the account they belong to."""
    win = _window()
    engine = win.broker.engine
    prices = {"AAPL": 100.0}
    win._prices.update(prices)
    expiry = date.today() + timedelta(days=30)
    engine.market_buy("AAPL", 100.0, quantity=5)
    engine.trade_option(OptionContract("AAPL", expiry, 100.0, OptionRight.CALL),
                        Side.BUY, 1, 3.0, prices)
    engine.trade_option(OptionContract("AAPL", expiry, 90.0, OptionRight.PUT),
                        Side.SELL, 1, 2.0, prices)

    original = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        win._reset_session()
    finally:
        QMessageBox.question = original

    s = win.session
    snap = win.broker.snapshot(prices, {})
    check("reset clears option positions", not s.option_positions)
    check("reset clears share positions", not s.positions)
    check("reset restores cash", abs(snap.cash - s.starting_balance) < 0.01)
    check("reset frees short-option collateral",
          abs(snap.buying_power - s.starting_balance) < 0.01)
    check("reset leaves the account at its starting value",
          abs(snap.total_value - s.starting_balance) < 0.01)
    win.close()


def test_unknown_symbol_never_joins_the_watchlist() -> None:
    """Free text is confirmed with the provider before it is adopted."""
    win = _window()
    before = win._watchlist.symbols()
    win._on_validate_done("ZZZZQQ", None, "ZZZZQQ: no data returned.")
    app.processEvents()
    check("rejected symbol is not added", win._watchlist.symbols() == before)
    check("rejection is explained", "⚠" in win.statusBar().currentMessage())

    quote, _candles = SyntheticProvider().fetch_chart("NVDA", "1D")
    win._on_validate_done("NVDA", quote, "")
    app.processEvents()
    check("validated symbol is added", win._watchlist.contains("NVDA"))
    check("validated symbol becomes active", win._active_symbol == "NVDA")
    win.close()


def test_layout_never_crushes_itself() -> None:
    """Every pane keeps at least its own minimum, at any size and in any view.

    Qt doesn't clip a widget that is given less than its minimum — it draws the
    children on top of each other (range tabs over the chart's time axis, one
    card over another). Checked down to the window's real minimum size, for
    the line, candle and options views.
    """
    win = _window()
    win._on_quote(_quote("AAPL", 100.0, 99.0))
    floor = win.minimumSize()
    sizes = ((floor.width(), floor.height()), (1440, 900), (1560, 980), (1800, 1120))
    for width, height in sizes:
        for view in ("line", "candles", "options"):
            win._set_market_mode("options" if view == "options" else "stock")
            if view != "options":
                win._chart._on_mode_clicked(view)
            win.resize(width, height)
            app.processEvents()
            panes = [("price hero", win._price_header)]
            if view == "options":
                panes += [("option ticket", win._option_ticket), ("chain", win._options_chain)]
            else:
                panes += [("order card", win._trade_panel), ("chart", win._chart)]
            if not win._day_stats.isHidden():
                panes.append(("key stats", win._day_stats))
            for name, widget in panes:
                need = widget.minimumSizeHint().height()
                check(f"{width}x{height} {view}: {name} keeps its height",
                      widget.height() >= need)
            if view != "options":
                check(f"{width}x{height} {view}: chart keeps usable height",
                      win._chart._plot.height() >= 180)
    win._set_market_mode("stock")
    win.resize(1560, 980)
    app.processEvents()
    check("a roomy window has space for the key statistics", not win._day_stats.isHidden())
    win._chart._on_mode_clicked("line")
    win.close()


def test_splitters_are_draggable_and_bounded() -> None:
    """The bars actually move, and neither pane can be dragged out of existence."""
    win = _window()
    win.resize(1560, 980)
    app.processEvents()

    before = win._main_split.sizes()
    win._main_split.setSizes([before[0] - 120, before[1] + 120])
    app.processEvents()
    check("vertical splitter responds to a drag", win._main_split.sizes() != before)

    # Drag it as far as it will go: the blotter must keep its tab bar and a row.
    win._main_split.setSizes([10_000, 0])
    app.processEvents()
    tab_bar = win._tabs.tabBar().height()
    check("blotter survives being dragged shut",
          win._tabs.height() >= tab_bar + 40 and win._tabs.tabBar().isVisible())

    cols = win._top_split.sizes()
    win._top_split.setSizes([cols[0] + 80, cols[1] - 80, cols[2]])
    app.processEvents()
    check("column splitter responds to a drag", win._top_split.sizes() != cols)

    win._top_split.setSizes([0, 10_000, 0])
    app.processEvents()
    squeezed = win._top_split.sizes()
    check("watchlist rail cannot be collapsed", squeezed[0] >= 150)
    check("order card cannot be collapsed", squeezed[2] >= 280)
    win.close()


def test_chart_controls_do_not_jump() -> None:
    """The row under the chart must not shuffle as the chart is used."""
    win = _window()
    win.resize(1560, 980)
    app.processEvents()
    chart = win._chart
    quote, candles = SyntheticProvider().fetch_chart("AAPL", "1D")
    chart.set_symbol("AAPL")
    chart.update_reference(quote.previous_close)
    chart.set_candles("AAPL", "1D", candles)
    app.processEvents()

    button = type(chart._reset_btn)
    tabs = [b for b in chart.findChildren(button) if b.objectName() == "RangeTab"]
    toggles = chart.mode_toggle.findChildren(button)
    check("range tabs and the Line/Candles switch are on screen",
          len(tabs) == 8 and len(toggles) == 2 and all(t.isVisible() for t in toggles))

    def where():
        return [b.mapTo(win, b.rect().topLeft()) for b in tabs + toggles]

    before = where()
    vb = chart._plot.getViewBox()
    first, last = candles[0].epoch, candles[-1].epoch
    mid = (first + last) / 2
    vb.setXRange(mid - (last - first) * 0.05, mid + (last - first) * 0.05, padding=0)
    chart._hover_label.setText("O $1,234.56   H $1,234.56   L $1,234.56   C $1,234.56")
    app.processEvents()
    check("reset button appears when zoomed", not chart._reset_btn.isHidden())
    check("tabs and Line/Candles stay put when the reset button and readout appear",
          where() == before)
    win.close()


def test_chart_chrome_switches() -> None:
    """The line view is bare by default and the Theme menu toggles the rest."""
    win = _window()
    chart = win._chart
    left = chart._plot.getAxis("left")
    bottom = chart._plot.getAxis("bottom")

    check("line view starts bare", not left.isVisible() and not bottom.isVisible())
    chart._on_mode_clicked("candles")
    app.processEvents()
    check("candles always keep their scales", left.isVisible() and bottom.isVisible())
    chart._on_mode_clicked("line")
    app.processEvents()
    check("line view goes bare again", not left.isVisible())

    win._chrome_actions["axes"].setChecked(True)
    app.processEvents()
    check("axes switch turns them on", left.isVisible() and bottom.isVisible())
    win._chrome_actions["grid"].setChecked(True)
    app.processEvents()
    check("gridline switch is recorded", chart.chrome()["grid"])
    win._chrome_actions["last_price"].setChecked(False)
    app.processEvents()
    check("last-price tag can be hidden", not chart._last_label.isVisible())

    # …and the choices survive a restart.
    settings = win._store.load_settings()
    check("chart switches persist",
          settings.get("chart_axes") is True and settings.get("chart_grid") is True
          and settings.get("chart_last_price") is False)
    win.close()

    reopened = _window()
    check("chart switches are restored",
          reopened._chart.chrome() == {"axes": True, "grid": True, "last_price": False})
    # Put the defaults back so later checks see a clean slate.
    for key, value in (("axes", False), ("grid", False), ("last_price", True)):
        reopened._chrome_actions[key].setChecked(value)
    app.processEvents()
    reopened.close()


def test_buy_button_keeps_its_fill() -> None:
    """The order card's pill must paint its own fill inside the scroll column.

    A selector-less stylesheet on the column's viewport once cascaded
    `background: transparent` to every descendant, leaving the enabled pill as
    near-black text on black. Checked on rendered pixels, not stylesheet text.
    """
    from PyQt6.QtGui import QColor

    win = _window()
    win._on_quote(_quote("AAPL", 100.0, 99.0))
    app.processEvents()
    panel = win._trade_panel
    panel._amount.setText("3")
    app.processEvents()
    button = panel._submit
    check("buy pill is enabled with a quantity", button.isEnabled())
    image = button.grab().toImage()
    dpr = image.devicePixelRatio()
    # Sample inside the pill but clear of the label.
    pixel = image.pixelColor(int(button.width() * 0.12 * dpr), int(button.height() * 0.5 * dpr))
    want = QColor(theme.PALETTES["dark"]["buy"])
    close = (abs(pixel.red() - want.red()) + abs(pixel.green() - want.green())
             + abs(pixel.blue() - want.blue())) < 40
    check(f"buy pill paints its green fill (got {pixel.name()})", close)
    panel._amount.setText("")
    app.processEvents()
    image = button.grab().toImage()
    pixel = image.pixelColor(int(button.width() * 0.12 * dpr), int(button.height() * 0.5 * dpr))
    check(f"disabled pill still has a visible fill (got {pixel.name()})",
          pixel.name() != "#000000")
    win.close()


def test_chart_scrub_drives_the_hero() -> None:
    """Hovering the chart shows that point's price in the hero; leaving restores it."""
    win = _window()
    win._on_quote(_quote("AAPL", 100.0, 98.0))
    _q, candles = SyntheticProvider().fetch_chart("AAPL", "1D")
    chart = win._chart
    chart.set_candles("AAPL", "1D", candles)
    app.processEvents()
    header = win._price_header
    target = candles[len(candles) // 3]
    vb = chart._plot.getViewBox()
    chart._on_mouse_moved(vb.mapViewToScene(QPointF(target.epoch, target.close)))
    app.processEvents()
    check("scrubbing shows the hovered price", header._price_label.text() == fmt_price(target.close))
    check("the change is measured to the hovered point",
          fmt_signed_money(target.close - 98.0) in header._change_label.text())
    chart._hide_crosshair()
    app.processEvents()
    check("leaving the chart restores the live price",
          header._price_label.text() == fmt_price(100.0))
    win.close()


def test_hero_follows_the_chart_range() -> None:
    """"Today" on 1D; "Past week", from the range's first price, on 1W."""
    win = _window()
    win._on_quote(_quote("AAPL", 110.0, 100.0))
    header = win._price_header
    text = header._change_label.text()
    check("1D measures from the previous close", "+$10.00" in text and "Today" in text)
    win._chart._on_range_clicked("1W")
    check("a new range shows no change until its data arrives",
          header._change_label.text() == "")
    _q, week = SyntheticProvider().fetch_chart("AAPL", "1W")
    win._on_chart("AAPL", "1W", week)
    start = week[0].open or week[0].close
    text = header._change_label.text()
    check("1W reads 'Past week'", "Past week" in text)
    check("1W measures from the range's first price", fmt_signed_money(110.0 - start) in text)
    win.close()


def test_accent_follows_the_displayed_change() -> None:
    """Like the reference, the page tints by the change the hero shows: orange
    on a down day, and by the range's own change on longer ranges."""
    from paper_trader.data.models import Candle

    win = _window()
    win._stop_feed()   # keep the demo feed from replacing the hand-fed data
    pill = win._trade_panel._submit
    win.set_active_symbol("AAPL")
    win._on_quote(_quote("AAPL", 95.0, 100.0))
    check("a down day turns the accent orange",
          pill.property("accent") == "down" and win._options_cta.property("accent") == "down")
    win._on_quote(_quote("AAPL", 105.0, 100.0))
    check("an up day turns it back", pill.property("accent") == "up")

    win._on_quote(_quote("AAPL", 95.0, 100.0))          # down on the day...
    win._chart._on_range_clicked("1M")
    check("a range change holds the accent until its bars arrive",
          pill.property("accent") == "down")
    now = datetime.now(timezone.utc)
    month = [Candle(time=now, open=80.0, high=81.0, low=79.0, close=80.5, volume=1.0),
             Candle(time=now, open=90.0, high=96.0, low=89.0, close=95.0, volume=1.0)]
    win._on_chart("AAPL", "1M", month)                   # ...but up on the month
    check("an up month on a down day is green",
          pill.property("accent") == "up" and win._chart._rising())
    check("the range tab follows a programmatic range change",
          [b.text() for b in win._chart._range_group.buttons() if b.isChecked()] == ["1M"])
    win.close()


def test_watchlist_rows_survive_a_list_change() -> None:
    """Adding a symbol rebuilds the list; the existing rows keep their data."""
    from paper_trader.ui.widgets.watchlist import _PRICE, _SPARK, WatchlistPanel

    panel = WatchlistPanel()
    panel.set_watchlist(["AAPL", "MSFT"])
    panel.update_quotes({"AAPL": _quote("AAPL", 101.0, 100.0)})
    panel.update_sparklines({"AAPL": [100.0, 100.5, 101.0]})
    panel.add_symbol("NVDA", select=False)
    item = panel._items["AAPL"]
    check("a row keeps its price across a rebuild", item.data(_PRICE) == 101.0)
    check("a row keeps its sparkline across a rebuild",
          tuple(item.data(_SPARK)) == (100.0, 100.5, 101.0))
    check("the new symbol joins the list", panel.symbols() == ["AAPL", "MSFT", "NVDA"])


def test_notices_surface_as_toasts() -> None:
    """Fills and errors show as toasts; an error one goes once data flows again."""
    win = _window()
    win._status("Bought 1 AAPL @ $100.00", 4000)
    check("a fill shows as a success toast",
          win._toast.text().startswith("Bought") and win._toast.kind() == "success")
    win._on_feed_error("network", "Rate limited by data provider.")
    check("an error shows as an error toast, icon instead of the ⚠",
          win._toast.kind() == "error" and "⚠" not in win._toast.text())
    win._on_quote(_quote("AAPL", 101.0, 100.0))
    app.processEvents()
    check("the error toast goes once data flows", win._toast.text() == "")
    win.close()


def test_money_never_reads_negative_zero() -> None:
    """Float noise (12 x 421.63 - 5059.56) must print as $0.00, not $-0.00."""
    noise = 12 * 421.63 - 5059.56
    check("no '$-0.00'", fmt_signed_money(noise) == "$0.00" and fmt_money(-0.0) == "$0.00")
    check("no '+-0.00%'", fmt_signed_pct(-0.0) == "+0.00%")
    check("a real loss keeps its sign", fmt_signed_money(-0.01) == "-$0.01")


def test_a_new_range_is_framed_whole() -> None:
    """Switching range frames the new series. The pan limits used to trail a
    render behind, clamping a month into the previous day's window."""
    provider = SyntheticProvider()
    quote, day = provider.fetch_chart("AAPL", "1D")
    _q, month = provider.fetch_chart("AAPL", "1M")
    w = ChartWidget()
    w.resize(1000, 480)
    w.show()
    w.set_symbol("AAPL")
    w.update_reference(quote.previous_close)
    w.set_candles("AAPL", "1D", day)
    app.processEvents()
    w._on_range_clicked("1M")
    w.set_candles("AAPL", "1M", month)
    app.processEvents()
    (x0, x1), _ = w._plot.getViewBox().viewRange()
    check("the whole month is in view", x0 <= w._xs[0] + 1 and x1 >= w._xs[-1] - 1)
    check("a fresh range doesn't offer 'Reset zoom'", not w._reset_btn.isVisible())
    w.close()


def test_multi_day_ranges_close_market_gaps() -> None:
    """Nights and weekends don't take up the chart: sessions sit side by side,
    and the axis still names the real days."""
    from paper_trader.ui.widgets.chart import close_gaps

    provider = SyntheticProvider()
    quote, day = provider.fetch_chart("AAPL", "1D")
    _q, week = provider.fetch_chart("AAPL", "1W")
    epochs = np.array([c.epoch for c in week])
    xs = close_gaps(epochs)
    steps = np.diff(xs)
    check("no step wider than a bar and a half", steps.max() <= np.median(steps) * 1.5)
    check("regular bars keep their spacing",
          np.allclose(steps[np.diff(epochs) <= 300], 300))
    w = ChartWidget()
    w.resize(1000, 480)
    w.show()
    w.set_symbol("AAPL")
    w._on_range_clicked("1W")
    w.set_candles("AAPL", "1W", week)
    app.processEvents()
    axis = w._time_axis
    (x0, x1), _ = w._plot.getViewBox().viewRange()
    ticks = axis.tickValues(x0, x1, 900)[0][1]
    labels = axis.tickStrings(ticks, 1.0, 1.0)
    days = sorted({datetime.fromtimestamp(c.epoch).astimezone().date() for c in week})
    check("the week's axis names its days",
          labels == [f"{d:%b} {d.day}" for d in days[1:]])
    w._on_range_clicked("1D")
    w.set_candles("AAPL", "1D", day)
    check("a single day stays on the clock",
          np.array_equal(w._xs, np.array([c.epoch for c in day])))
    w.close()


def test_small_print_scales_from_the_stylesheet_font() -> None:
    """Delegates derive their small/bold text from a px-sized stylesheet font.

    Point arithmetic on such a font (pointSizeF() == -1) used to fall back to a
    flat 8pt — 8px on macOS — so watchlist names and search tags shrank there.
    """
    from PyQt6.QtGui import QFont

    px = QFont(); px.setPixelSize(13)
    pt = QFont(); pt.setPointSizeF(10.0)
    check("px font shrinks in px", theme.resized(px, -2).pixelSize() == 11)
    check("px font grows in px", theme.resized(px, 1).pixelSize() == 14)
    check("pt font keeps its unit", abs(theme.resized(pt, -2).pointSizeF() - 8.5) < 0.01)
    check("resized never hits zero", theme.resized(px, -40).pixelSize() >= 1)


def test_blotter_drops_columns_it_cannot_fit() -> None:
    """A narrow blotter hides its least important columns instead of scrolling."""
    from paper_trader.core.portfolio import PositionView

    view = PositionView(symbol="AAPL", quantity=6, avg_cost=406.46, cost_basis=2438.76,
                        price=407.0, priced=True, market_value=2442.0,
                        unrealized_pl=3.24, unrealized_pl_pct=0.13,
                        day_change=3.24, day_change_pct=0.13, weight=0.24)
    t = PositionsTable()
    t.update_positions([view])
    t.resize(430, 220)
    t.show()
    app.processEvents()
    header = t._table.horizontalHeader()
    hidden = {c for c in range(t._table.columnCount()) if header.isSectionHidden(c)}
    check("a narrow blotter hides average cost first", 2 in hidden and hidden <= {1, 2, 3})
    check("symbol, value, today and total return stay",
          not hidden & {0, 4, 5, 6})
    t.resize(1200, 220)
    app.processEvents()
    check("a wide blotter shows every column",
          not any(header.isSectionHidden(c) for c in range(t._table.columnCount())))
    check("the dropped weight lives on as a tooltip", "of your portfolio" in
          (t._table.item(0, 4).toolTip() or ""))
    t.close()


def test_watchlist_can_be_hidden_and_stays_hidden() -> None:
    """The rail folds away (the chart takes the room) and remembers it."""
    win = _window()
    win._toggle_watchlist()
    app.processEvents()
    check("the watchlist hides", win._rail.isHidden())
    check("the choice is saved", win._store.load_settings().get("show_watchlist") is False)
    win.close()
    again = _window()
    check("and it is restored on the next launch", again._rail.isHidden())
    again._set_watchlist_visible(True)
    again.close()


def test_legacy_window_still_runs() -> None:
    """`run.py --old` keeps the previous interface working."""
    from paper_trader.ui_legacy.main_window import MainWindow as LegacyWindow

    theme.apply_theme(app, "dark", legacy=True)
    store = Store()
    session = Session.new("Legacy", 10_000.0, ["AAPL", "MSFT"])
    store.save_session(session)
    win = LegacyWindow(session, store, broker_mode="local", data_source="demo",
                       theme_name="dark")
    win.resize(1440, 900)
    win.show()
    app.processEvents()
    check("legacy window builds", win._chart is not None and win._watchlist is not None)
    check("legacy watchlist keeps its own search box", hasattr(win._watchlist, "_search"))
    win._on_quote(_quote("AAPL", 101.0, 100.0))
    app.processEvents()
    check("legacy window takes quotes", win._prices.get("AAPL") == 101.0)
    win._chart._on_mode_clicked("candles")
    win._chart._on_mode_clicked("line")
    check("legacy chart toggles views", win._chart._mode == "line")
    win.close()
    theme.apply_theme(app, "dark", legacy=False)


def main() -> int:
    for fn in (test_chart_zoom_refits_price_axis,
               test_chart_drops_the_previous_symbol,
               test_tables_show_their_empty_state,
               test_orders_table_separates_dollars_from_shares,
               test_theme_switch_keeps_the_price_visible,
               test_fill_message_survives_the_next_tick,
               test_reset_clears_option_positions,
               test_unknown_symbol_never_joins_the_watchlist,
               test_layout_never_crushes_itself,
               test_splitters_are_draggable_and_bounded,
               test_chart_controls_do_not_jump,
               test_chart_chrome_switches,
               test_buy_button_keeps_its_fill,
               test_chart_scrub_drives_the_hero,
               test_hero_follows_the_chart_range,
               test_accent_follows_the_displayed_change,
               test_watchlist_rows_survive_a_list_change,
               test_notices_surface_as_toasts,
               test_money_never_reads_negative_zero,
               test_small_print_scales_from_the_stylesheet_font,
               test_a_new_range_is_framed_whole,
               test_multi_day_ranges_close_market_gaps,
               test_blotter_drops_columns_it_cannot_fit,
               test_watchlist_can_be_hidden_and_stays_hidden,
               test_legacy_window_still_runs):
        fn()
    failed = [name for name, ok in _checks if not ok]
    for name, ok in _checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{len(_checks) - len(failed)}/{len(_checks)} checks passed.")
    if failed:
        print("FAILED:", failed)
        return 1
    print("ALL UI TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
