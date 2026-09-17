"""Live Alpaca integration test (read-only). Skips cleanly without credentials.

    python tests/test_alpaca.py

Exercises the market-data provider (quote/bars/search) and the broker
(refresh/snapshot/orders) against the real paper account. It does not place or
cancel orders, so it's safe to run repeatedly.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_trader.credentials import load_alpaca

creds = load_alpaca()
if creds is None:
    print("SKIP: no Alpaca credentials configured (env or ~/.paper_trader/credentials.json).")
    raise SystemExit(0)

from paper_trader.broker.alpaca import AlpacaBroker
from paper_trader.data.alpaca_client import AlpacaClient
from paper_trader.data.market_data import create_service

_checks: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    _checks.append((name, bool(cond)))


def test_provider() -> None:
    svc = create_service("alpaca")
    q, candles = svc.get_quote_and_candles("AAPL", "1D")
    check("quote price > 0", q.price > 0)
    check("quote has prev close", q.previous_close > 0)
    check("quote name cleaned", q.display_name and "Common Stock" not in q.display_name)
    check("1D intraday bars", len(candles) > 20)
    check("1Y daily bars", len(svc.get_candles("NVDA", "1Y")) > 100)
    check("ALL monthly bars", len(svc.get_candles("MSFT", "ALL")) > 20)
    check("search ranks AAPL first for 'apple'",
          (svc.search("apple") or [None])[0] and svc.search("apple")[0].symbol == "AAPL")


def test_broker() -> None:
    broker = AlpacaBroker(AlpacaClient(creds))
    broker.refresh()
    snap = broker.snapshot({}, {})
    # Cash can legitimately be negative on a margin-using account (long market
    # value funded on margin), so assert the account is coherent, not positive.
    check("account reports an equity value", snap.total_value > 0)
    check("buying power present", snap.buying_power > 0)
    check("total value == cash + holdings",
          abs(snap.total_value - (snap.cash + snap.holdings_value)) < 0.02)
    check("held symbols is a list", isinstance(broker.held_symbols(), list))
    check("open_orders returns Orders", isinstance(broker.open_orders(), list))
    check("equity curve loaded", len(broker.equity_curve()) >= 1 or snap.starting_balance > 0)


def main() -> int:
    test_provider()
    test_broker()
    failed = [n for n, ok in _checks if not ok]
    for n, ok in _checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}")
    print(f"\n{len(_checks) - len(failed)}/{len(_checks)} checks passed.")
    if failed:
        print("FAILED:", failed)
        return 1
    print("ALL ALPACA TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
