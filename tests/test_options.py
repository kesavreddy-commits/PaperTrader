"""Options tests: Black-Scholes pricing, the trading engine, valuation, chain and
persistence.

Plain-assert style so it runs with no test framework:

    python tests/test_options.py

Exits non-zero if anything fails.
"""

import math
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PAPER_TRADER_HOME", tempfile.mkdtemp(prefix="pt_opt_test_"))

from paper_trader.core.engine import (
    InsufficientFundsError,
    InvalidOrderError,
    TradingEngine,
)
from paper_trader.core.models import OptionPosition, Session, Side
from paper_trader.core.options import (
    CONTRACT_MULTIPLIER,
    OptionContract,
    OptionRight,
    black_scholes,
    collateral_per_contract,
    greeks,
    price_contract,
)
from paper_trader.core.portfolio import Portfolio
from paper_trader.data.options_chain import OptionsChainService
from paper_trader.persistence.store import Store

_checks: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    _checks.append((name, bool(cond)))


def approx(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


def _future(days: int = 30) -> date:
    return date.today() + timedelta(days=days)


# --------------------------------------------------------------------------- #
def test_black_scholes() -> None:
    S, K, T, vol = 100.0, 100.0, 0.5, 0.30
    call = black_scholes(OptionRight.CALL, S, K, T, vol)
    put = black_scholes(OptionRight.PUT, S, K, T, vol)
    # Put-call parity: C - P = S - K e^{-rT}.
    from paper_trader.core.options import RISK_FREE_RATE as r
    check("put-call parity", approx(call - put, S - K * math.exp(-r * T), 1e-6))
    check("call value positive", call > 0)

    # Deep ITM call ≈ intrinsic (discounted); OTM call cheaper than ATM.
    deep = black_scholes(OptionRight.CALL, 200.0, 100.0, T, vol)
    check("deep ITM call ~ intrinsic", deep > 100.0 and deep < 105.0)
    otm = black_scholes(OptionRight.CALL, 100.0, 120.0, T, vol)
    check("OTM call cheaper than ATM", otm < call)

    # At expiry, value collapses to intrinsic.
    check("expiry call intrinsic", approx(black_scholes(OptionRight.CALL, 110, 100, 0, vol), 10.0))
    check("expiry put worthless OTM", approx(black_scholes(OptionRight.PUT, 110, 100, 0, vol), 0.0))

    # Delta bounds.
    dc, gc, tc, vc = greeks(OptionRight.CALL, S, K, T, vol)
    dp, *_ = greeks(OptionRight.PUT, S, K, T, vol)
    check("call delta in (0,1)", 0.0 < dc < 1.0)
    check("put delta in (-1,0)", -1.0 < dp < 0.0)
    check("gamma positive", gc > 0)
    check("theta negative (long)", tc < 0)


def test_occ_roundtrip() -> None:
    c = OptionContract("AAPL", date(2025, 12, 19), 150.0, OptionRight.CALL)
    occ = c.occ_symbol
    check("occ format", occ == "AAPL251219C00150000")
    check("occ round-trip", OptionContract.parse(occ) == c)
    check("is_option_symbol true", OptionContract.is_option_symbol(occ))
    check("is_option_symbol false", not OptionContract.is_option_symbol("AAPL"))
    frac = OptionContract("SPY", date(2026, 1, 16), 512.5, OptionRight.PUT)
    check("fractional strike round-trip", OptionContract.parse(frac.occ_symbol) == frac)


def test_open_close_long() -> None:
    s = Session.new("T", 50_000.0, ["AAPL"])
    eng = TradingEngine(s)
    c = OptionContract("AAPL", _future(), 150.0, OptionRight.CALL)
    occ = c.occ_symbol
    pm = {"AAPL": 155.0}

    eng.trade_option(c, Side.BUY, 3, 6.00, pm)  # buy to open
    check("BTO builds long", eng.option_position_quantity(occ) == 3)
    check("BTO debits cash", approx(s.cash, 50_000 - 3 * 6.00 * 100))

    eng.trade_option(c, Side.BUY, 2, 8.00, pm)  # add
    check("re-average premium", approx(s.option_positions[occ].avg_price, (3 * 6 + 2 * 8) / 5, 1e-6))

    avg = s.option_positions[occ].avg_price
    t = eng.trade_option(c, Side.SELL, 5, 9.00, pm)  # sell to close all
    check("STC closes long", occ not in s.option_positions)
    check("STC realizes P/L", approx(t.realized_pl, 5 * (9.00 - avg) * 100, 0.01))
    check("realized accrues to session", approx(s.realized_pl, 5 * (9.00 - avg) * 100, 0.01))


def test_short_and_collateral() -> None:
    s = Session.new("T", 50_000.0, ["AAPL"])
    eng = TradingEngine(s)
    put = OptionContract("AAPL", _future(), 150.0, OptionRight.PUT)
    occ = put.occ_symbol
    pm = {"AAPL": 155.0}

    eng.trade_option(put, Side.SELL, 2, 4.00, pm)  # sell to open (short)
    check("STO builds short", eng.option_position_quantity(occ) == -2)
    check("STO credits cash", approx(s.cash, 50_000 + 2 * 4.00 * 100))
    col = eng.total_option_collateral(pm)
    check("put collateral = strike*100*qty", approx(col, 150.0 * 100 * 2))
    check("buying power = cash - collateral", approx(eng.option_buying_power(pm), s.cash - col))

    # Over-shorting beyond buying power is rejected.
    try:
        eng.trade_option(put, Side.SELL, 100, 4.00, pm)
        check("over-short rejected", False)
    except InsufficientFundsError:
        check("over-short rejected", True)

    # Buy to close releases collateral and realizes P/L.
    t = eng.trade_option(put, Side.BUY, 2, 2.50, pm)
    check("BTC closes short", occ not in s.option_positions)
    check("short realized (avg-price)", approx(t.realized_pl, 2 * (4.00 - 2.50) * 100, 0.01))
    check("collateral released", eng.total_option_collateral(pm) == 0.0)


def test_flip_through_zero() -> None:
    s = Session.new("T", 100_000.0, ["AAPL"])
    eng = TradingEngine(s)
    c = OptionContract("AAPL", _future(), 100.0, OptionRight.CALL)
    occ = c.occ_symbol
    pm = {"AAPL": 100.0}
    eng.trade_option(c, Side.BUY, 2, 5.00, pm)   # long 2
    eng.trade_option(c, Side.SELL, 5, 6.00, pm)  # sell 5 -> flip to short 3
    check("flips to short", eng.option_position_quantity(occ) == -3)
    check("new avg is fill price", approx(s.option_positions[occ].avg_price, 6.00, 1e-6))


def test_expiration_settlement() -> None:
    s = Session.new("T", 50_000.0, ["AAPL"])
    eng = TradingEngine(s)
    exp = date.today() - timedelta(days=1)
    long_itm = OptionContract("AAPL", exp, 150.0, OptionRight.CALL)   # ITM at 160
    short_otm = OptionContract("AAPL", exp, 140.0, OptionRight.PUT)   # OTM at 160 (short keeps premium)
    short_itm = OptionContract("AAPL", exp, 170.0, OptionRight.CALL)  # ITM at 160 -> assigned
    s.option_positions[long_itm.occ_symbol] = OptionPosition(long_itm.occ_symbol, 1, 2.0)
    s.option_positions[short_otm.occ_symbol] = OptionPosition(short_otm.occ_symbol, -1, 1.5)
    s.option_positions[short_itm.occ_symbol] = OptionPosition(short_itm.occ_symbol, -1, 1.0)

    c0 = s.cash
    settled = eng.settle_expirations({"AAPL": 160.0})
    check("all expired settled", len(settled) == 3 and not s.option_positions)
    # Long ITM call intrinsic 10 -> +1000 cash. Short OTM put -> 0. Short ITM call
    # (strike 170, spot 160) is actually OTM -> 0. So net cash change = +1000.
    check("settlement cash flow", approx(s.cash, c0 + 1000.0))
    notes = {t.note for t in settled}
    check("exercise recorded", "Exercised" in notes)


def test_deferred_settlement_without_price() -> None:
    s = Session.new("T", 50_000.0, [])
    eng = TradingEngine(s)
    exp = date.today() - timedelta(days=1)
    c = OptionContract("AAPL", exp, 150.0, OptionRight.CALL)
    s.option_positions[c.occ_symbol] = OptionPosition(c.occ_symbol, 1, 2.0)
    settled = eng.settle_expirations({})  # no price for AAPL
    check("settlement deferred without price", not settled and c.occ_symbol in s.option_positions)


def test_portfolio_with_options() -> None:
    s = Session.new("T", 50_000.0, ["AAPL"])
    eng = TradingEngine(s)
    call = OptionContract("AAPL", _future(), 150.0, OptionRight.CALL)
    put = OptionContract("AAPL", _future(), 150.0, OptionRight.PUT)
    pm = {"AAPL": 155.0}
    eng.trade_option(call, Side.BUY, 2, price_contract(call, 155.0).ask, pm)
    eng.trade_option(put, Side.SELL, 1, price_contract(put, 155.0).bid, pm)

    snap = Portfolio(s).snapshot(pm, {})
    check("total = cash + holdings", approx(snap.total_value, snap.cash + snap.holdings_value))
    check("buying power nets collateral", approx(snap.buying_power, snap.cash - snap.options_collateral))
    check("two option views", len(snap.option_positions) == 2)
    check("collateral for the short put", approx(snap.options_collateral, 150.0 * 100))
    long_view = next(v for v in snap.option_positions if v.quantity > 0)
    check("long option market value positive", long_view.market_value > 0)
    short_view = next(v for v in snap.option_positions if v.quantity < 0)
    check("short option market value negative", short_view.market_value < 0)


def test_chain_service() -> None:
    svc = OptionsChainService()
    exps = svc.expirations()
    check("expirations are future", all(e > date.today() for e in exps))
    check("expirations sorted & unique", exps == sorted(set(exps)))
    price = 187.34
    chain = svc.chain("AAPL", price, exps[0])
    strikes = [r.strike for r in chain.rows]
    check("ladder brackets the spot", min(strikes) < price < max(strikes))
    check("atm strike near spot", abs(chain.atm_strike - price) <= 5.0)
    row = min(chain.rows, key=lambda r: abs(r.strike - price))
    check("atm call has ~0.5 delta", 0.3 < row.call.delta < 0.7)
    check("bid <= mark <= ask", row.call.bid <= row.call.mark <= row.call.ask)


def test_persistence_roundtrip() -> None:
    s = Session.new("Opt Round Trip", 30_000.0, ["AAPL"])
    eng = TradingEngine(s)
    call = OptionContract("AAPL", _future(), 150.0, OptionRight.CALL)
    put = OptionContract("AAPL", _future(), 150.0, OptionRight.PUT)
    pm = {"AAPL": 155.0}
    eng.trade_option(call, Side.BUY, 2, 6.0, pm)
    eng.trade_option(put, Side.SELL, 1, 3.0, pm)
    store = Store()
    store.save_session(s)
    loaded = store.load_session(s.id)
    check("option round-trip preserves everything", loaded.to_dict() == s.to_dict())
    check("schema version is 2", s.to_dict()["schema_version"] == 2)
    check("option trade marked as option",
          any(t.is_option and t.multiplier == 100 for t in loaded.trades))


def main() -> int:
    for fn in (test_black_scholes, test_occ_roundtrip, test_open_close_long,
               test_short_and_collateral, test_flip_through_zero,
               test_expiration_settlement, test_deferred_settlement_without_price,
               test_portfolio_with_options, test_chain_service,
               test_persistence_roundtrip):
        fn()
    failed = [name for name, ok in _checks if not ok]
    for name, ok in _checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{len(_checks) - len(failed)}/{len(_checks)} checks passed.")
    if failed:
        print("FAILED:", failed)
        return 1
    print("ALL OPTION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
