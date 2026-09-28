"""Application-wide configuration: filesystem paths, numeric precision,
polling cadence and other tunable constants.

Everything here is plain data — no side effects at import time except the
resolution of the application data directory (which is created lazily by the
persistence layer, not here).
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Filesystem
# --------------------------------------------------------------------------- #
# All local state lives under a single directory in the user's home folder so
# the app is trivially portable and easy to reset (just delete the folder).
# Override with the PAPER_TRADER_HOME environment variable if desired.
APP_NAME = "Paper Trader"


def app_home() -> Path:
    """Return the root directory for all locally persisted state."""
    override = os.environ.get("PAPER_TRADER_HOME")
    root = Path(override).expanduser() if override else Path.home() / ".paper_trader"
    return root


def sessions_dir() -> Path:
    return app_home() / "sessions"


def settings_path() -> Path:
    return app_home() / "settings.json"


# --------------------------------------------------------------------------- #
# Money / share precision
# --------------------------------------------------------------------------- #
# Prices and cash are rounded to cents; fractional shares to 1e-6 (matching the
# granularity most brokers expose). Rounding is centralised in ``util`` so the
# whole engine stays consistent.
MONEY_DECIMALS = 2
SHARE_DECIMALS = 6

# Guard against absurd/typo orders in the simulator.
MIN_ORDER_VALUE = 0.01

# --------------------------------------------------------------------------- #
# Session defaults
# --------------------------------------------------------------------------- #
DEFAULT_STARTING_BALANCE = 10_000.00
DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "SPY"]

# --------------------------------------------------------------------------- #
# Market-data polling
# --------------------------------------------------------------------------- #
# The background feed ticks on this interval. On each tick it always refreshes
# the active symbol's quote; the chart and watchlist refresh every Nth tick to
# spread network load and stay well within Yahoo's informal rate limits.
FEED_TICK_SECONDS = 4.0
CHART_REFRESH_EVERY_TICKS = 8      # ~32s at a 4s tick
WATCHLIST_REFRESH_EVERY_TICKS = 4  # ~16s at a 4s tick
# Watchlist sparklines (a day of closes per row) refresh slowly and a couple of
# symbols at a time, so a long watchlist never bursts the rate limit.
SPARKLINE_REFRESH_SECONDS = 300.0
SPARKLINES_PER_TICK = 2
SPARKLINE_POINTS = 64

# Cache time-to-live per data kind (seconds). Multiple UI consumers asking for
# the same symbol within the TTL share one network response.
QUOTE_TTL = 3.0
CHART_TTL = 20.0
SEARCH_TTL = 300.0

# HTTP behaviour
HTTP_TIMEOUT = 8.0
HTTP_MAX_RETRIES = 2
HTTP_BACKOFF_SECONDS = 0.75

# How often the session is auto-saved to disk (in addition to save-on-trade).
AUTOSAVE_SECONDS = 20.0

# How often a remote broker (Alpaca) account/positions/orders are re-polled.
# Position market-values already update on every price tick (snapshot overlays
# live prices on cached positions with no network), so this poll only needs to
# catch fills/cash changes — a slower cadence keeps us well under the rate cap.
BROKER_POLL_SECONDS = 4.0
# Fills/activities are heavier and change rarely — poll them every Nth refresh.
BROKER_ACTIVITIES_EVERY = 5

# Alpaca enforces ~200 API calls/min per account. We throttle all Alpaca
# requests (across the data feed, broker poll and order actions, which share the
# key) to stay safely under this via a sliding-window limiter, keeping headroom.
ALPACA_MAX_CALLS_PER_MIN = 180
ALPACA_RATE_WINDOW_SECONDS = 60.0

# --------------------------------------------------------------------------- #
# Chart ranges -> Yahoo (range, interval) pairs
# --------------------------------------------------------------------------- #
# Ordered mapping used to build the range selector. Intervals are chosen to
# keep each series at a sensible resolution (a few hundred points).
CHART_RANGES: dict[str, tuple[str, str]] = {
    "1D": ("1d", "1m"),
    "1W": ("5d", "5m"),
    "1M": ("1mo", "30m"),
    "3M": ("3mo", "1d"),
    "YTD": ("ytd", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1wk"),
    "ALL": ("max", "1mo"),
}
DEFAULT_RANGE = "1D"

# --------------------------------------------------------------------------- #
# UI defaults
# --------------------------------------------------------------------------- #
DEFAULT_THEME = "dark"  # "dark" | "light"
