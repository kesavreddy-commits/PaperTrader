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

from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication([])
app.setStyle("Fusion")

from paper_trader.core.models import Order, OrderStatus, Session, Side  # noqa: E402
from paper_trader.core.options import OptionContract, OptionRight  # noqa: E402
from paper_trader.data.models import Quote  # noqa: E402
from paper_trader.data.market_data import SyntheticProvider  # noqa: E402
from paper_trader.persistence.store import Store  # noqa: E402
from paper_trader.ui import anim, theme  # noqa: E402
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

        mid = (x0 + x1) / 2
        half = (x1 - x0) * 0.05
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
    """Every pane must keep at least its own minimum at any window size.

    Setting an explicit minimumHeight on a pane overrides the minimum its layout
    computed, which is how the order card once ended up drawn on top of itself.
    """
    win = _window()
    for width, height in ((1160, 820), (1440, 900), (1560, 980), (1800, 1120)):
        win.resize(width, height)
        app.processEvents()
        for name, widget in (("order card", win._trade_panel),
                             ("price hero", win._price_header),
                             ("day stats", win._day_stats)):
            need = widget.minimumSizeHint().height()
            check(f"{width}x{height}: {name} keeps its height",
                  widget.height() >= need)
        check(f"{width}x{height}: chart keeps usable height",
              win._chart._plot.height() >= 180)
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

    buttons = [b for b in chart.findChildren(type(chart._reset_btn))
               if b.text() in ("Line", "Candles")]
    before = [b.x() for b in buttons]
    vb = chart._plot.getViewBox()
    (x0, x1), _ = vb.viewRange()
    mid = (x0 + x1) / 2
    vb.setXRange(mid - (x1 - x0) * 0.05, mid + (x1 - x0) * 0.05, padding=0)
    chart._hover_label.setText("O $1,234.56   H $1,234.56   L $1,234.56   C $1,234.56")
    app.processEvents()
    check("Line/Candles stay put when the reset button and readout appear",
          [b.x() for b in buttons] == before)
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
