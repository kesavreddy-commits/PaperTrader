"""Data-layer tests: the synthetic provider and the Yahoo response parser.

    python tests/test_data.py

The Yahoo parser is tested against a captured-shape payload (no network needed),
which is what de-risks the live path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_trader.data.market_data import (
    InvalidSymbolError,
    MarketDataService,
    SyntheticProvider,
    YahooProvider,
    create_service,
)

_checks: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    _checks.append((name, bool(cond)))


def test_synthetic_provider() -> None:
    svc = create_service("demo")
    q, candles = svc.get_quote_and_candles("AAPL", "1D")
    check("quote has a positive price", q.price > 0)
    check("previous close set", q.previous_close > 0)
    check("intraday candles present", len(candles) > 100)
    check("chart end matches header price", abs(candles[-1].close - q.price) < 0.05)

    long_candles = svc.get_candles("AAPL", "1Y")
    check("1Y candles present", len(long_candles) > 100)

    results = svc.search("AAP")
    check("search returns AAPL", any(r.symbol == "AAPL" for r in results))
    check("search allows arbitrary ticker", any(r.symbol == "ZZZ" for r in svc.search("ZZZ")))


def test_synthetic_is_deterministic_but_live() -> None:
    p = SyntheticProvider()
    # The walk is seeded per symbol+range, so the *shape* is identical across
    # calls; only the overall level is rescaled to track the live price. Verify
    # the normalised series (each close / final close) matches — scale cancels.
    a = p.fetch_chart("MSFT", "1M")[1]
    b = p.fetch_chart("MSFT", "1M")[1]
    na = [c.close / a[-1].close for c in a]
    nb = [c.close / b[-1].close for c in b]
    max_diff = max(abs(x - y) for x, y in zip(na, nb))
    check("chart shape is stable across refreshes", max_diff < 1e-3)
    # And the level stays close between back-to-back calls (smooth live drift).
    check("live level drifts only slightly", abs(a[-1].close / b[-1].close - 1) < 0.01)


def test_synthetic_ranges_span_their_labels() -> None:
    """Demo bars sit on exchange hours and cover the span each range names —
    "Past week" is five trading days, not a day and a half of round-the-clock
    bars — at any time of day, weekends included."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    p = SyntheticProvider()
    for label, when in (("Tue 11:15", datetime(2026, 9, 29, 11, 15, tzinfo=et)),
                        ("Mon 07:00 (pre-market)", datetime(2026, 9, 28, 7, 0, tzinfo=et)),
                        ("Sat noon", datetime(2026, 9, 26, 12, 0, tzinfo=et))):
        now = when.timestamp()
        local = {rk: [datetime.fromtimestamp(t, et) for t in p._timeline(rk, now)]
                 for rk in ("1D", "1W", "1M", "3M", "1Y")}
        check(f"{label}: no bar in the future", all(
            t.timestamp() <= now for bars in local.values() for t in bars))
        check(f"{label}: bars on weekdays only", all(
            t.weekday() < 5 for bars in local.values() for t in bars))
        day = local["1D"]
        check(f"{label}: 1D is one session, 4:00-20:00 ET",
              len({t.date() for t in day}) == 1
              and day[0].hour == 4 and day[-1].hour < 20)
        week = local["1W"]
        check(f"{label}: 1W is five trading days of regular hours",
              len({t.date() for t in week}) == 5
              and all((9, 30) <= (t.hour, t.minute) < (16, 0) for t in week))
        span = lambda bars: (bars[-1] - bars[0]).days
        check(f"{label}: 1M spans about a month", 26 <= span(local["1M"]) <= 31)
        check(f"{label}: 3M spans about three months", 85 <= span(local["3M"]) <= 92)
        check(f"{label}: 1Y spans about a year", 360 <= span(local["1Y"]) <= 366)
    check("sessions by the Eastern clock",
          [p._market_state(datetime(2026, 9, 28, h, m, tzinfo=et).timestamp())
           for h, m in ((3, 0), (7, 0), (10, 0), (17, 0), (21, 0))]
          == ["CLOSED", "PRE", "REGULAR", "POST", "CLOSED"])
    q, day = p.fetch_chart("AAPL", "1D")
    q_month, _ = p.fetch_chart("AAPL", "1M")
    check("key stats describe the day whatever the chart shows",
          (q.day_high, q.day_low, q.day_open) == (q_month.day_high, q_month.day_low, q_month.day_open))
    check("the day opens near yesterday's close", abs(day[0].open / q.previous_close - 1) < 0.01)


def test_yahoo_parser() -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": "AAPL", "currency": "USD", "exchangeName": "NMS",
                        "regularMarketPrice": 229.35, "chartPreviousClose": 227.52,
                        "regularMarketVolume": 41234567, "shortName": "Apple Inc.",
                        "longName": "Apple Inc.", "marketState": "REGULAR",
                    },
                    "timestamp": [1723122600, 1723122660, 1723122720, 1723122780],
                    "indicators": {
                        "quote": [{
                            "open": [227.6, 228.1, None, 229.0],
                            "high": [228.2, 228.9, None, 229.6],
                            "low": [227.4, 227.9, None, 228.7],
                            "close": [228.0, 228.6, None, 229.35],
                            "volume": [10000, 12000, None, 15000],
                        }]
                    },
                }
            ],
            "error": None,
        }
    }
    q, candles = YahooProvider._parse_chart("AAPL", payload)
    check("yahoo price parsed", q.price == 229.35)
    check("yahoo change computed", abs(q.change - 1.83) < 1e-6)
    check("null-padded bar skipped", len(candles) == 3)

    try:
        YahooProvider._parse_chart("ZZZZ", {"chart": {"result": None, "error": {
            "code": "Not Found", "description": "No data found, symbol may be delisted"}}})
        check("invalid symbol raises", False)
    except InvalidSymbolError:
        check("invalid symbol raises", True)


def test_cache_shares_requests() -> None:
    calls = {"n": 0}

    class CountingProvider(SyntheticProvider):
        def fetch_chart(self, symbol, range_key):
            calls["n"] += 1
            return super().fetch_chart(symbol, range_key)

    svc = MarketDataService(CountingProvider())
    svc.get_quote("AAPL")
    svc.get_quote("AAPL")  # served from cache within TTL
    check("TTL cache collapses duplicate quote fetches", calls["n"] == 1)


def main() -> int:
    for fn in (test_synthetic_provider, test_synthetic_is_deterministic_but_live,
               test_synthetic_ranges_span_their_labels,
               test_yahoo_parser, test_cache_shares_requests):
        fn()
    failed = [name for name, ok in _checks if not ok]
    for name, ok in _checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{len(_checks) - len(failed)}/{len(_checks)} checks passed.")
    if failed:
        print("FAILED:", failed)
        return 1
    print("ALL DATA TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
