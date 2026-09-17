"""Core trading-engine, portfolio, analytics and persistence tests.

Plain-assert style so it runs with no test framework:

    python tests/test_engine.py

Exits non-zero if anything fails.
"""

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PAPER_TRADER_HOME", tempfile.mkdtemp(prefix="pt_test_"))

from paper_trader.core.analytics import compute_analytics
from paper_trader.core.engine import (
    InsufficientFundsError,
    InsufficientSharesError,
    InvalidOrderError,
    TradingEngine,
)
from paper_trader.config import sessions_dir
from paper_trader.core.models import EquityPoint, OrderStatus, Session, Side
from paper_trader.core.options import OptionContract, OptionRight
from paper_trader.core.portfolio import Portfolio
from paper_trader.persistence.store import Store

_checks: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    _checks.append((name, bool(cond)))


def approx(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


def test_market_orders_and_validation() -> None:
    s = Session.new("T", 10_000.0, ["AAPL"])
    eng = TradingEngine(s)

    t = eng.market_buy("AAPL", 150.0, notional=3000.0)
    qty = s.positions["AAPL"].quantity
    check("buy by dollars sizes shares", approx(qty, 20.0, 1e-6))
    check("buy debits cash", approx(s.cash, 10_000 - qty * 150))
    check("buy never overspends budget", qty * 150 <= 3000.0 + 1e-9)

    eng.market_buy("AAPL", 160.0, quantity=10)
    check("weighted average cost", approx(s.positions["AAPL"].avg_cost, (20 * 150 + 10 * 160) / 30, 1e-4))

    try:
        eng.market_sell("AAPL", 170.0, quantity=999)
        check("oversell rejected", False)
    except InsufficientSharesError:
        check("oversell rejected", True)

    try:
        eng.market_buy("AAPL", 150.0, notional=1e9)
        check("overspend rejected", False)
    except InsufficientFundsError:
        check("overspend rejected", True)

    try:
        eng.market_buy("AAPL", 0.0, quantity=1)
        check("bad price rejected", False)
    except InvalidOrderError:
        check("bad price rejected", True)

    avg = s.positions["AAPL"].avg_cost
    tr = eng.market_sell("AAPL", 170.0, quantity=15)
    check("realized P/L correct", approx(tr.realized_pl, 15 * (170 - avg), 0.02))
    check("realized accumulates on session", approx(s.realized_pl, 15 * (170 - avg), 0.02))

    eng.market_sell("AAPL", 175.0, sell_all=True)
    check("sell_all closes position", "AAPL" not in s.positions)
    check("cash-flow signs consistent",
          approx(s.cash, 10_000 + s.realized_pl, 0.05))


def test_limit_orders() -> None:
    s = Session.new("T", 10_000.0, [])
    eng = TradingEngine(s)
    eng.place_limit("MSFT", Side.BUY, 5, 250.0)

    fills = eng.process_pending({"MSFT": 260.0})
    check("buy-limit does not fill above limit", not fills and "MSFT" not in s.positions)

    fills = eng.process_pending({"MSFT": 249.0})
    check("buy-limit fills at/under limit", len(fills) == 1 and approx(s.positions["MSFT"].quantity, 5))
    check("limit fills at market price (improvement)", approx(fills[0].price, 249.0))

    # A sell-limit reserves shares from further sell-limits.
    try:
        eng.place_limit("MSFT", Side.SELL, 4, 300.0)
        eng.place_limit("MSFT", Side.SELL, 4, 310.0)  # only 1 uncommitted share left
        check("sell-limit honours committed shares", False)
    except InsufficientSharesError:
        check("sell-limit honours committed shares", True)


def test_portfolio() -> None:
    s = Session.new("T", 10_000.0, [])
    eng = TradingEngine(s)
    eng.market_buy("MSFT", 250.0, quantity=5)
    snap = Portfolio(s).snapshot({"MSFT": 255.0}, {"MSFT": 248.0})
    check("total = cash + holdings", approx(snap.total_value, snap.cash + snap.holdings_value))
    check("holdings valued at live price", approx(snap.holdings_value, 5 * 255.0))
    check("unrealized P/L", approx(snap.unrealized_pl, 5 * (255 - 250), 0.02))
    check("day change vs prev close", approx(snap.day_change, 5 * (255 - 248), 0.02))
    check("weights sum to ~holdings share",
          approx(sum(p.weight for p in snap.positions), snap.holdings_value / snap.total_value, 0.001))


def test_analytics() -> None:
    s = Session.new("T", 10_000.0, [])
    base = datetime.now(timezone.utc)
    s.equity_curve = [EquityPoint(base - timedelta(days=4 - i), 10_000 * (1 + 0.01 * i)) for i in range(5)]
    r = compute_analytics(s)
    check("analytics counts days", r.days == 5)
    check("sharpe computed with enough data", r.sharpe_ratio is not None)
    check("drawdown non-positive", r.max_drawdown_pct is not None and r.max_drawdown_pct <= 1e-6)


def test_persistence_roundtrip() -> None:
    s = Session.new("Round Trip", 5_000.0, ["AAPL", "MSFT"])
    eng = TradingEngine(s)
    eng.market_buy("AAPL", 190.0, quantity=3)
    eng.place_limit("MSFT", Side.BUY, 2, 100.0)
    store = Store()
    store.save_session(s)
    loaded = store.load_session(s.id)
    check("round-trip preserves everything", loaded.to_dict() == s.to_dict())
    check("session appears in listing", any(i.id == s.id for i in store.list_sessions()))


def test_committed_shares_block_market_sells() -> None:
    """Shares reserved by a resting sell limit are not sellable twice."""
    s = Session.new("T", 10_000.0, ["AAPL"])
    eng = TradingEngine(s)
    eng.market_buy("AAPL", 100.0, quantity=10)
    eng.place_limit("AAPL", Side.SELL, 8, 130.0)

    try:
        eng.market_sell("AAPL", 110.0, quantity=5)
        check("market sell cannot use committed shares", False)
    except InsufficientSharesError:
        check("market sell cannot use committed shares", True)

    t = eng.market_sell("AAPL", 110.0, quantity=2)
    check("uncommitted shares still sellable", approx(t.quantity, 2.0, 1e-9))

    eng.market_buy("AAPL", 100.0, quantity=10)
    sold = eng.market_sell("AAPL", 110.0, sell_all=True)
    check("sell-all leaves the committed shares alone",
          approx(sold.quantity, 10.0, 1e-6) and approx(s.positions["AAPL"].quantity, 8.0, 1e-6))


def test_prune_inactive_orders() -> None:
    """The order list stays bounded while keeping every live order."""
    s = Session.new("T", 1_000_000.0, ["AAPL"])
    eng = TradingEngine(s)
    for i in range(70):
        o = eng.place_limit("AAPL", Side.BUY, 1, 50.0 + i)
        o.status = OrderStatus.CANCELLED
    live = eng.place_limit("AAPL", Side.BUY, 1, 10.0)
    eng.prune_inactive_orders(keep=50)
    kept = s.pending_orders
    check("pruning bounds the order list", len(kept) == 51)
    check("pruning never drops a live order", any(o.id == live.id for o in kept))
    check("pruning keeps the newest inactive orders",
          all(o.status is OrderStatus.PENDING or o.limit_price >= 70.0 for o in kept))


def test_session_listing_survives_a_bad_timestamp() -> None:
    """A session file with no updated_at must not break the whole listing."""
    store = Store()
    good = Session.new("Good", 10_000.0, ["AAPL"])
    store.save_session(good)
    payload = good.to_dict()
    payload["id"] = "notimestamp01"
    payload["name"] = "No timestamp"
    payload.pop("updated_at")
    path = sessions_dir() / "notimestamp01.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        infos = store.list_sessions()
    except TypeError:
        check("session listing tolerates a missing timestamp", False)
        return
    check("session listing tolerates a missing timestamp",
          any(i.id == "notimestamp01" for i in infos) and any(i.id == good.id for i in infos))


def test_analytics_counts_option_closes() -> None:
    """Buy-to-close books real P/L, so it belongs in the win/loss statistics."""
    s = Session.new("T", 10_000.0, [])
    eng = TradingEngine(s)
    expiry = date.today() + timedelta(days=30)
    contract = OptionContract("AAPL", expiry, 100.0, OptionRight.CALL)
    prices = {"AAPL": 100.0}
    eng.trade_option(contract, Side.SELL, 1, 3.00, prices)   # sell to open
    eng.trade_option(contract, Side.BUY, 1, 1.00, prices)    # buy to close: +$200

    report = compute_analytics(s)
    check("option close counts as a closing trade", report.win_rate == 100.0)
    check("option close feeds avg win", report.avg_win is not None and report.avg_win > 0)


def main() -> int:
    for fn in (test_market_orders_and_validation, test_limit_orders, test_portfolio,
               test_analytics, test_persistence_roundtrip,
               test_committed_shares_block_market_sells, test_prune_inactive_orders,
               test_session_listing_survives_a_bad_timestamp,
               test_analytics_counts_option_closes):
        fn()
    failed = [name for name, ok in _checks if not ok]
    for name, ok in _checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{len(_checks) - len(failed)}/{len(_checks)} checks passed.")
    if failed:
        print("FAILED:", failed)
        return 1
    print("ALL CORE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
