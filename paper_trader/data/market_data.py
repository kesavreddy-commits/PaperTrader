"""Market-data provider and service facade.

Design
------
``MarketDataProvider`` is an abstract interface with two operations: fetch a
chart (which also yields a current-price :class:`Quote`, since Yahoo returns
both in one response) and search for symbols. ``YahooProvider`` implements it
against Yahoo Finance's public JSON endpoints — free and key-less, which keeps
launch friction at zero. To use Alpha Vantage or Polygon instead, implement the
same interface and pass it to :class:`MarketDataService`.

``MarketDataService`` wraps a provider with a TTL cache (so repeated requests
for the same symbol share one network round-trip) and typed error handling.
Throttling of *how often* we poll lives in the UI's feed thread; the cache here
guarantees we never exceed that regardless of how many widgets ask.

Only this module and its provider touch the network.
"""

from __future__ import annotations

import hashlib
import math
import random
import threading
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

# US market hours are defined in Eastern time; extended sessions are pre-market
# 04:00–09:30 ET and after-hours 16:00–20:00 ET.
_ET = ZoneInfo("America/New_York")

from .alpaca_client import AlpacaClient, AlpacaError

from ..config import (
    CHART_RANGES,
    CHART_TTL,
    DEFAULT_RANGE,
    HTTP_BACKOFF_SECONDS,
    HTTP_MAX_RETRIES,
    HTTP_TIMEOUT,
    QUOTE_TTL,
    SEARCH_TTL,
)
from .cache import TTLCache
from .models import Candle, Quote, SearchResult


# --------------------------------------------------------------------------- #
# Typed errors — the UI distinguishes these to show the right message.
# --------------------------------------------------------------------------- #
class MarketDataError(Exception):
    """Base class for all data-layer failures."""


class InvalidSymbolError(MarketDataError):
    """The requested symbol does not exist or has no data."""


class NetworkError(MarketDataError):
    """A transport/HTTP failure talking to the provider (offline, rate limit…)."""


# --------------------------------------------------------------------------- #
# Provider interface
# --------------------------------------------------------------------------- #
class MarketDataProvider(ABC):
    """Pluggable market-data backend."""

    @abstractmethod
    def fetch_chart(self, symbol: str, range_key: str) -> tuple[Quote, list[Candle]]:
        """Return the current quote and OHLCV bars for ``symbol`` over the given
        range key (one of :data:`config.CHART_RANGES`).

        Raises :class:`InvalidSymbolError` for unknown symbols and
        :class:`NetworkError` for connectivity/rate-limit problems.
        """

    @abstractmethod
    def search(self, query: str) -> list[SearchResult]:
        """Return symbol-search hits for a free-text ``query``."""

    def fetch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Return quotes for many symbols. Default loops :meth:`fetch_chart`;
        providers with a batch endpoint (Alpaca) override this to use one call."""
        out: dict[str, Quote] = {}
        for sym in symbols:
            try:
                out[sym] = self.fetch_chart(sym, DEFAULT_RANGE)[0]
            except MarketDataError:
                continue
        return out


# --------------------------------------------------------------------------- #
# Yahoo Finance implementation
# --------------------------------------------------------------------------- #
class YahooProvider(MarketDataProvider):
    """Talks to Yahoo Finance's open ``chart`` and ``search`` endpoints."""

    CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"

    # A realistic browser UA avoids most 403s from Yahoo's edge.
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(self._HEADERS)
        self._crumb: str | None = None
        self._primed = False
        # The feed thread and the symbol-search pool thread share this provider;
        # requests.Session is not documented as thread-safe, and the priming
        # handshake below must not run twice concurrently.
        self._io_lock = threading.Lock()

    def _ensure_primed(self) -> None:
        """Best-effort: acquire Yahoo's consent cookie and an API crumb.

        Yahoo gates its JSON endpoints behind a session cookie and, for some of
        them, a per-session 'crumb'. We fetch both once, ignoring failures — the
        chart endpoint usually works with the cookie alone, so a missing crumb is
        not fatal. If a later request is rejected as unauthorised we drop the
        primed flag so the next call re-primes (self-healing).
        """
        with self._io_lock:
            if self._primed:
                return
            self._primed = True  # attempt only once until explicitly reset
            try:
                # Sets Yahoo's consent cookies on the session (404 is fine).
                self._session.get("https://fc.yahoo.com", timeout=HTTP_TIMEOUT)
            except requests.RequestException:
                pass
            try:
                resp = self._session.get(
                    "https://query1.finance.yahoo.com/v1/test/getcrumb",
                    timeout=HTTP_TIMEOUT,
                )
                text = resp.text.strip()
                # A valid crumb is short and contains no whitespace or HTML.
                if resp.status_code == 200 and text and "<" not in text and len(text) < 40:
                    self._crumb = text
            except requests.RequestException:
                pass

    # -- public API -------------------------------------------------------- #
    def fetch_chart(self, symbol: str, range_key: str) -> tuple[Quote, list[Candle]]:
        self._ensure_primed()
        yahoo_range, yahoo_interval = CHART_RANGES[range_key]
        url = self.CHART_URL.format(symbol=requests.utils.quote(symbol))
        params = {
            "range": yahoo_range,
            "interval": yahoo_interval,
            "includePrePost": "false",
        }
        if self._crumb:
            params["crumb"] = self._crumb
        payload = self._request(url, params)
        return self._parse_chart(symbol, payload)

    def search(self, query: str) -> list[SearchResult]:
        self._ensure_primed()
        params = {
            "q": query,
            "quotesCount": 12,
            "newsCount": 0,
            "listsCount": 0,
            "enableFuzzyQuery": "false",
        }
        if self._crumb:
            params["crumb"] = self._crumb
        payload = self._request(self.SEARCH_URL, params)
        results: list[SearchResult] = []
        for item in payload.get("quotes", []):
            sym = item.get("symbol")
            if not sym:
                continue
            results.append(
                SearchResult(
                    symbol=sym,
                    name=item.get("shortname") or item.get("longname") or "",
                    exchange=item.get("exchDisp", ""),
                    type=item.get("quoteType") or item.get("typeDisp", ""),
                )
            )
        return results

    # -- HTTP with retry/backoff ------------------------------------------- #
    def _request(self, url: str, params: dict) -> dict:
        """GET ``url`` and return parsed JSON, retrying transient failures.

        404 responses are returned (not retried): the chart endpoint uses 404
        with a structured ``chart.error`` body for unknown symbols, which the
        caller inspects.
        """
        last_error: MarketDataError | None = None
        for attempt in range(HTTP_MAX_RETRIES + 1):
            try:
                with self._io_lock:
                    resp = self._session.get(url, params=params, timeout=HTTP_TIMEOUT)
            except requests.Timeout as exc:
                last_error = NetworkError(f"Request timed out: {exc}")
            except requests.RequestException as exc:
                # DNS failure, connection refused, no route to host, etc.
                last_error = NetworkError(f"Network error: {exc}")
            else:
                if resp.status_code in (401, 403):
                    # Likely a stale/invalid crumb — force a re-prime next call.
                    self._primed = False
                    self._crumb = None
                    raise NetworkError(
                        f"Data provider denied access (HTTP {resp.status_code}). "
                        "It may be temporarily rate-limiting this machine."
                    )
                if resp.status_code == 429:
                    last_error = NetworkError("Rate limited by data provider (HTTP 429).")
                elif resp.status_code >= 500:
                    last_error = NetworkError(f"Provider server error (HTTP {resp.status_code}).")
                else:
                    # 200 or 404 — both carry a JSON body we want.
                    try:
                        return resp.json()
                    except ValueError:
                        last_error = NetworkError("Provider returned a non-JSON response.")

            # Backoff before the next attempt (linear; small).
            if attempt < HTTP_MAX_RETRIES:
                time.sleep(HTTP_BACKOFF_SECONDS * (attempt + 1))

        raise last_error or NetworkError("Unknown network failure.")

    # -- parsing ----------------------------------------------------------- #
    @staticmethod
    def _parse_chart(symbol: str, payload: dict) -> tuple[Quote, list[Candle]]:
        chart = payload.get("chart") or {}
        error = chart.get("error")
        if error:
            desc = error.get("description") or error.get("code") or "unknown error"
            # Yahoo reports unknown symbols this way.
            raise InvalidSymbolError(f"{symbol}: {desc}")

        results = chart.get("result")
        if not results:
            raise InvalidSymbolError(f"{symbol}: no data returned.")

        r0 = results[0]
        meta = r0.get("meta") or {}

        price = meta.get("regularMarketPrice")
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None:
            raise InvalidSymbolError(f"{symbol}: provider returned no price.")
        if prev_close is None:
            prev_close = price  # avoids a spurious 100% "change" on IPO/first day

        candles = YahooProvider._parse_candles(r0)

        # Prefer meta for day stats; fall back to computing from candles.
        day_high = meta.get("regularMarketDayHigh")
        day_low = meta.get("regularMarketDayLow")
        day_open = meta.get("regularMarketOpen")
        if candles:
            if day_high is None:
                day_high = max(c.high for c in candles)
            if day_low is None:
                day_low = min(c.low for c in candles)
            if day_open is None:
                day_open = candles[0].open

        quote = Quote(
            symbol=meta.get("symbol", symbol).upper(),
            price=float(price),
            previous_close=float(prev_close),
            day_high=_as_float(day_high),
            day_low=_as_float(day_low),
            day_open=_as_float(day_open),
            volume=_as_float(meta.get("regularMarketVolume")),
            currency=meta.get("currency", "USD"),
            exchange=meta.get("exchangeName") or meta.get("fullExchangeName", ""),
            short_name=meta.get("shortName", ""),
            long_name=meta.get("longName", ""),
            market_state=meta.get("marketState", ""),
            timestamp=datetime.now(timezone.utc),
        )
        return quote, candles

    @staticmethod
    def _parse_candles(result: dict) -> list[Candle]:
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators") or {}
        quote_block = (indicators.get("quote") or [{}])[0]
        opens = quote_block.get("open") or []
        highs = quote_block.get("high") or []
        lows = quote_block.get("low") or []
        closes = quote_block.get("close") or []
        volumes = quote_block.get("volume") or []

        candles: list[Candle] = []
        for i, ts in enumerate(timestamps):
            # Yahoo pads gaps (holidays, halts) with nulls — skip those bars.
            o = _at(opens, i)
            h = _at(highs, i)
            low = _at(lows, i)
            c = _at(closes, i)
            if c is None or o is None or h is None or low is None:
                continue
            candles.append(
                Candle(
                    time=datetime.fromtimestamp(ts, tz=timezone.utc),
                    open=float(o),
                    high=float(h),
                    low=float(low),
                    close=float(c),
                    volume=float(_at(volumes, i) or 0.0),
                )
            )
        return candles


def _at(seq: list, i: int):
    return seq[i] if i < len(seq) else None


def _as_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _et_date(iso: str | None):
    """Parse an Alpaca clock timestamp (e.g. next_open) to its Eastern date."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone(_ET).date()
    except (TypeError, ValueError):
        return None


def _clean_name(name: str) -> str:
    """Trim verbose corporate suffixes Alpaca appends (e.g. 'Common Stock')."""
    if not name:
        return ""
    for suffix in (" Common Stock", " Common Shares", " Ordinary Shares",
                   " Class A Common Stock"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


# --------------------------------------------------------------------------- #
# Cached, throttled service facade
# --------------------------------------------------------------------------- #
class MarketDataService:
    """Caching front-end to a :class:`MarketDataProvider`.

    All methods normalise the symbol (upper-cased, trimmed) and serve from a TTL
    cache when possible. A single Yahoo chart response yields both a quote and a
    candle series, so fetching a chart also refreshes the quote cache for free.
    """

    def __init__(self, provider: MarketDataProvider | None = None) -> None:
        self._provider = provider or YahooProvider()
        self._quotes: TTLCache[Quote] = TTLCache(QUOTE_TTL)
        self._charts: TTLCache[list[Candle]] = TTLCache(CHART_TTL)
        self._searches: TTLCache[list[SearchResult]] = TTLCache(SEARCH_TTL)

    # -- helpers ----------------------------------------------------------- #
    @staticmethod
    def normalize(symbol: str) -> str:
        return symbol.strip().upper()

    @staticmethod
    def _chart_key(symbol: str, range_key: str) -> str:
        return f"{symbol}@{range_key}"

    # -- quotes ------------------------------------------------------------ #
    def get_quote(self, symbol: str) -> Quote:
        symbol = self.normalize(symbol)
        cached = self._quotes.get(symbol)
        if cached is not None:
            return cached
        # Prefer the provider's quote-only path (Alpaca: one snapshot, no bars).
        quote = self._provider.fetch_quotes([symbol]).get(symbol)
        if quote is None:
            # Fall back to a full chart fetch (also validates the symbol).
            quote, _candles = self._fetch(symbol, DEFAULT_RANGE)
            return quote
        self._quotes.set(symbol, quote)
        return quote

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Return quotes for several symbols, serving cached ones and batching
        the misses into a single provider call where supported."""
        wanted = [self.normalize(s) for s in symbols]
        result: dict[str, Quote] = {}
        missing: list[str] = []
        for sym in wanted:
            cached = self._quotes.get(sym)
            if cached is not None:
                result[sym] = cached
            else:
                missing.append(sym)
        if missing:
            for sym, quote in self._provider.fetch_quotes(missing).items():
                key = self.normalize(sym)
                self._quotes.set(key, quote)
                result[key] = quote
        return result

    def get_candles(self, symbol: str, range_key: str) -> list[Candle]:
        symbol = self.normalize(symbol)
        key = self._chart_key(symbol, range_key)
        cached = self._charts.get(key)
        if cached is not None:
            return cached
        _, candles = self._fetch(symbol, range_key)
        return candles

    def get_quote_and_candles(
        self, symbol: str, range_key: str
    ) -> tuple[Quote, list[Candle]]:
        """Return both with at most one network call (used by the feed thread)."""
        symbol = self.normalize(symbol)
        cached_quote = self._quotes.get(symbol)
        cached_candles = self._charts.get(self._chart_key(symbol, range_key))
        if cached_quote is not None and cached_candles is not None:
            return cached_quote, cached_candles
        return self._fetch(symbol, range_key)

    def validate_symbol(self, symbol: str) -> Quote:
        """Confirm a symbol exists (used before adding to a watchlist/trading).

        Returns its current quote or raises :class:`InvalidSymbolError`.
        """
        return self.get_quote(symbol)

    # -- search ------------------------------------------------------------ #
    def search(self, query: str) -> list[SearchResult]:
        query = query.strip()
        if not query:
            return []
        cached = self._searches.get(query.lower())
        if cached is not None:
            return cached
        results = self._provider.search(query)
        self._searches.set(query.lower(), results)
        return results

    # -- internal ---------------------------------------------------------- #
    def _fetch(self, symbol: str, range_key: str) -> tuple[Quote, list[Candle]]:
        quote, candles = self._provider.fetch_chart(symbol, range_key)
        self._quotes.set(symbol, quote)
        self._charts.set(self._chart_key(symbol, range_key), candles)
        return quote, candles


# --------------------------------------------------------------------------- #
# Synthetic (demo / offline) provider
# --------------------------------------------------------------------------- #
class SyntheticProvider(MarketDataProvider):
    """Deterministic, network-free market data for demo and offline use.

    Generates realistic random-walk prices so the *entire* application — charts,
    the trading engine, portfolio math and persistence — runs with no internet
    connection, or when the live provider is rate-limiting. The code paths for
    trading, P/L and persistence are identical to live mode; only the price
    numbers are synthetic. Prices drift smoothly and update every poll so the
    real-time behaviour of the UI is faithfully demonstrated.
    """

    # range_key -> (bar_seconds, per_bar_volatility). How many bars a range
    # holds follows from the span its label names, laid on exchange hours by
    # :meth:`_timeline` — so "Past week" really is five trading days.
    _RANGE_SHAPE = {
        "1D": (60, 0.0006),
        "1W": (300, 0.0011),
        "1M": (1800, 0.0016),
        "3M": (86_400, 0.014),
        "YTD": (86_400, 0.014),
        "1Y": (86_400, 0.014),
        "5Y": (604_800, 0.030),
        "ALL": (2_592_000, 0.05),
    }
    _PRE_OPEN, _OPEN, _CLOSE, _POST_CLOSE = dtime(4, 0), dtime(9, 30), dtime(16, 0), dtime(20, 0)

    # A small universe used for search and friendly names; any other symbol is
    # still tradable — it just gets a generated price and its ticker as a name.
    _UNIVERSE = [
        ("AAPL", "Apple Inc.", "EQUITY"),
        ("MSFT", "Microsoft Corporation", "EQUITY"),
        ("NVDA", "NVIDIA Corporation", "EQUITY"),
        ("TSLA", "Tesla, Inc.", "EQUITY"),
        ("AMZN", "Amazon.com, Inc.", "EQUITY"),
        ("GOOGL", "Alphabet Inc.", "EQUITY"),
        ("META", "Meta Platforms, Inc.", "EQUITY"),
        ("NFLX", "Netflix, Inc.", "EQUITY"),
        ("AMD", "Advanced Micro Devices, Inc.", "EQUITY"),
        ("INTC", "Intel Corporation", "EQUITY"),
        ("JPM", "JPMorgan Chase & Co.", "EQUITY"),
        ("V", "Visa Inc.", "EQUITY"),
        ("DIS", "The Walt Disney Company", "EQUITY"),
        ("KO", "The Coca-Cola Company", "EQUITY"),
        ("SPY", "SPDR S&P 500 ETF Trust", "ETF"),
        ("QQQ", "Invesco QQQ Trust", "ETF"),
        ("VTI", "Vanguard Total Stock Market ETF", "ETF"),
    ]
    _NAMES = {sym: name for sym, name, _ in _UNIVERSE}

    def fetch_chart(self, symbol: str, range_key: str) -> tuple[Quote, list[Candle]]:
        symbol = symbol.upper()
        now = time.time()
        candles = self._series(symbol, range_key, now)
        price = candles[-1].close if candles else self._current_price(symbol, now)
        anchor = self._anchor(symbol)
        name = self._NAMES.get(symbol, "")
        # Key statistics describe the trading day's regular session, whatever
        # range the chart is showing.
        day = candles if range_key == "1D" else self._series(symbol, "1D", now)
        regular = [c for c in day if self._OPEN <= c.time.astimezone(_ET).time() < self._CLOSE]
        session = regular or day
        quote = Quote(
            symbol=symbol,
            price=round(price, 2),
            previous_close=round(anchor, 2),
            day_high=round(max(c.high for c in session), 2) if session else price,
            day_low=round(min(c.low for c in session), 2) if session else price,
            day_open=round(session[0].open, 2) if session else price,
            volume=float(sum(c.volume for c in session)),
            currency="USD",
            exchange="DEMO",
            short_name=name or symbol,
            long_name=name,
            market_state=self._market_state(now),
            timestamp=datetime.now(timezone.utc),
        )
        return quote, candles

    def search(self, query: str) -> list[SearchResult]:
        q = query.strip().upper()
        if not q:
            return []
        hits: list[SearchResult] = []
        for sym, name, typ in self._UNIVERSE:
            if q in sym or q in name.upper():
                hits.append(SearchResult(symbol=sym, name=name, exchange="DEMO", type=typ))
        # If nothing matched, let the user add any plausible ticker anyway so
        # demo mode isn't limited to the built-in universe.
        if not hits and q.isalnum() and 1 <= len(q) <= 6:
            hits.append(SearchResult(symbol=q, name="", exchange="DEMO", type="EQUITY"))
        return hits[:12]

    # -- deterministic price model ---------------------------------------- #
    @staticmethod
    def _hash(text: str) -> int:
        return int(hashlib.sha256(text.encode()).hexdigest(), 16)

    def _anchor(self, symbol: str) -> float:
        """Yesterday's close — a stable per-symbol base price in $20..$500."""
        return round(20 + (self._hash(symbol) % 48_000) / 100.0, 2)

    def _phase(self, symbol: str) -> float:
        return (self._hash(symbol + "phase") % 100_000) / 100_000 * 2 * math.pi

    def _current_price(self, symbol: str, now: float) -> float:
        anchor = self._anchor(symbol)
        phase = self._phase(symbol)
        day = 0.02 * math.sin((now / 86_400) * 2 * math.pi + phase)  # intraday trend
        wiggle = 0.004 * math.sin(now / 300 + phase)                 # 5-minute wiggle
        live = 0.0007 * math.sin(now / 11 + phase)                   # sub-minute drift
        return max(0.01, anchor * (1 + day + wiggle + live))

    def _timeline(self, range_key: str, now: float) -> list[int]:
        """Bar start times (epoch seconds, oldest first) for ``range_key`` up to
        ``now``, on exchange hours the way a live feed returns them.

        1D is today's session — or the last one, before 4:00 ET and on
        weekends — including pre-market and after-hours; 1W and 1M are
        regular-hours bars over the last five / ~21 trading days; the daily,
        weekly and monthly ranges cover the calendar span their label names.
        Weekdays stand in for trading days (demo data needs no holiday table).
        """
        step = self._RANGE_SHAPE.get(range_key, self._RANGE_SHAPE["1D"])[0]
        et = datetime.fromtimestamp(now, _ET)

        def last_day(start_of_day: dtime) -> date:
            """The latest weekday whose session (from ``start_of_day``) has begun."""
            day = et.date()
            if et.time() < start_of_day:
                day -= timedelta(days=1)
            while day.weekday() >= 5:
                day -= timedelta(days=1)
            return day

        def weekdays(end: date, *, count: int = 0, since: date | None = None) -> list[date]:
            days: list[date] = []
            day = end
            while (count and len(days) < count) or (since is not None and day >= since):
                if day.weekday() < 5:
                    days.append(day)
                day -= timedelta(days=1)
            return days[::-1]

        def at(day: date, when: dtime) -> int:
            return int(datetime.combine(day, when, _ET).timestamp())

        def intraday(days: list[date], start: dtime, stop: dtime) -> list[int]:
            times: list[int] = []
            for day in days:
                t, close = at(day, start), at(day, stop)
                while t < close and t <= now:
                    times.append(t)
                    t += step
            return times

        if range_key == "1D":
            return intraday([last_day(self._PRE_OPEN)], self._PRE_OPEN, self._POST_CLOSE)
        end = last_day(self._OPEN)
        if range_key == "1W":
            return intraday(weekdays(end, count=5), self._OPEN, self._CLOSE)
        if range_key == "1M":
            return intraday(weekdays(end, since=end - timedelta(days=30)), self._OPEN, self._CLOSE)
        if range_key in ("3M", "YTD", "1Y"):
            since = {"3M": end - timedelta(days=91),
                     "YTD": date(end.year, 1, 1),
                     "1Y": end - timedelta(days=365)}[range_key]
            return [at(d, self._OPEN) for d in weekdays(end, since=since)]
        if range_key == "5Y":
            monday = end - timedelta(days=end.weekday())
            weeks = [monday - timedelta(weeks=i) for i in range(5 * 52)]
            return [at(d, self._OPEN) for d in reversed(weeks)]
        # ALL: the first weekday of each month for twenty years.
        months: list[int] = []
        year, month = end.year, end.month
        for _ in range(240):
            first = date(year, month, 1)
            while first.weekday() >= 5:
                first += timedelta(days=1)
            if first <= end:
                months.append(at(first, self._OPEN))
            year, month = (year, month - 1) if month > 1 else (year - 1, 12)
        return months[::-1]

    def _series(self, symbol: str, range_key: str, now: float) -> list[Candle]:
        step, vol = self._RANGE_SHAPE.get(range_key, self._RANGE_SHAPE["1D"])
        times = self._timeline(range_key, now) or [int(now)]
        bars = len(times)
        target = self._current_price(symbol, now)
        anchor = self._anchor(symbol)
        if range_key == "1D":
            # A fresh walk each trading day, pinned at both ends: it opens near
            # yesterday's close (a small pre-market gap) and ends on the live
            # price, so the day's change on the chart is the quote's change.
            session_day = datetime.fromtimestamp(times[0], _ET).date()
            rng = random.Random(f"{symbol}:1D:{session_day.isoformat()}")
            start = math.log(anchor * (1 + rng.uniform(-0.006, 0.006)))
            walk = [0.0]
            for _ in range(bars - 1):
                walk.append(walk[-1] + rng.gauss(0, vol))
            miss = walk[-1] - (math.log(target) - start)
            span = max(bars - 1, 1)
            closes = [math.exp(start + w - miss * i / span) for i, w in enumerate(walk)]
            drift = 0.0
        else:
            rng = random.Random(f"{symbol}:{range_key}")
            # Stable random walk (seed depends only on symbol+range, not on time).
            closes = []
            price = anchor * (1 + rng.uniform(-0.05, 0.05))
            drift = rng.uniform(-0.0002, 0.0004)
            for _ in range(bars):
                price = max(0.01, price * (1 + drift + rng.gauss(0, vol)))
                closes.append(price)
            # Scale the whole series so its final close equals the live current
            # price. Between polls the live price moves only fractions of a
            # percent, so historical bars stay visually stable while the last
            # bar tracks live.
            scale = target / closes[-1]
            closes = [c * scale for c in closes]

        candles: list[Candle] = []
        for i, (ts, close) in enumerate(zip(times, closes)):
            when = datetime.fromtimestamp(ts, tz=timezone.utc)
            open_ = closes[i - 1] if i > 0 else close * (1 - drift)
            body_hi, body_lo = max(open_, close), min(open_, close)
            wick = close * vol * (0.6 + (i % 5) * 0.18)
            volume = 500_000 * (1 + 0.5 * math.sin(i / 7.0)) + (i % 13) * 1000
            if step < 86_400 and not self._OPEN <= when.astimezone(_ET).time() < self._CLOSE:
                volume *= 0.12  # pre-market and after-hours trade thinly
            candles.append(
                Candle(
                    time=when,
                    open=round(open_, 4),
                    high=round(body_hi + wick, 4),
                    low=round(max(0.01, body_lo - wick), 4),
                    close=round(close, 4),
                    volume=float(int(volume)),
                )
            )
        return candles

    @staticmethod
    def _market_state(now: float) -> str:
        """US equity session by the Eastern clock (weekdays; no holiday table)."""
        et = datetime.fromtimestamp(now, _ET)
        if et.weekday() >= 5:
            return "CLOSED"
        t = et.time()
        if SyntheticProvider._PRE_OPEN <= t < SyntheticProvider._OPEN:
            return "PRE"
        if SyntheticProvider._OPEN <= t < SyntheticProvider._CLOSE:
            return "REGULAR"
        if SyntheticProvider._CLOSE <= t < SyntheticProvider._POST_CLOSE:
            return "POST"
        return "CLOSED"


# --------------------------------------------------------------------------- #
# Alpaca market-data provider
# --------------------------------------------------------------------------- #
class AlpacaProvider(MarketDataProvider):
    """Market data from Alpaca's data API (snapshots + bars) and assets list.

    The live price comes from the IEX snapshot (undelayed); historical bars use
    the default feed and end 16 minutes in the past to satisfy the free plan's
    recency rule. Symbol search filters Alpaca's tradable-assets list locally.
    """

    # range_key -> (timeframe, lookback_days, is_intraday). YTD uses Jan 1.
    _RANGES = {
        "1D": ("5Min", 5, True),
        "1W": ("15Min", 8, True),
        "1M": ("1Hour", 33, False),
        "3M": ("1Day", 95, False),
        "YTD": ("1Day", None, False),
        "1Y": ("1Day", 370, False),
        "5Y": ("1Week", 1830, False),
        "ALL": ("1Month", 4000, False),
    }

    def __init__(self, client: AlpacaClient) -> None:
        self._client = client
        self._assets: list[tuple[str, str, str]] = []  # (symbol, name, exchange)
        self._asset_names: dict[str, str] = {}
        self._assets_loaded = False
        self._clock_cache: tuple[dict, float] | None = None  # (clock_dict, monotonic_fetch_time)

    def fetch_chart(self, symbol: str, range_key: str) -> tuple[Quote, list[Candle]]:
        symbol = symbol.upper()
        quote = self._quote(symbol)
        candles = self._bars(symbol, range_key)
        # Extend an intraday series with the live price so the line reaches "now"
        # — but only during an active session. When the market has been closed
        # for hours/days (weekends), the gap would draw a long flat line, so skip.
        if candles and range_key in ("1D", "1W") and quote.price:
            last = candles[-1]
            gap = quote.timestamp.timestamp() - last.epoch
            if 0 < gap < 3600:  # within an hour of the last bar
                candles = candles + [Candle(
                    time=quote.timestamp, open=last.close,
                    high=max(last.close, quote.price), low=min(last.close, quote.price),
                    close=quote.price, volume=0.0)]
        return quote, candles

    def search(self, query: str) -> list[SearchResult]:
        self._ensure_assets()
        q = query.strip().upper()
        if not q:
            return []
        # Rank: exact ticker, then ticker-prefix, then name-starts-with, then any
        # substring. Within each tier prefer shorter tickers (common stocks have
        # short symbols; leveraged/ETF derivatives have longer ones).
        exact, sym_prefix, name_prefix, contains = [], [], [], []
        for sym, name, exch in self._assets:
            upper_name = name.upper()
            if sym == q:
                exact.append((sym, name, exch))
            elif sym.startswith(q):
                sym_prefix.append((sym, name, exch))
            elif upper_name.startswith(q):
                name_prefix.append((sym, name, exch))
            elif q in sym or q in upper_name:
                contains.append((sym, name, exch))
        by_symlen = lambda t: (len(t[0]), t[0])
        ranked = (exact + sorted(sym_prefix, key=by_symlen)
                  + sorted(name_prefix, key=by_symlen)
                  + sorted(contains, key=by_symlen))[:12]
        return [SearchResult(symbol=s, name=_clean_name(n), exchange=e, type="EQUITY")
                for s, n, e in ranked]

    # -- internals --------------------------------------------------------- #
    def fetch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Batch quotes in a single multi-symbol snapshot request (rate-friendly)."""
        syms = [s.upper() for s in symbols]
        if not syms:
            return {}
        try:
            snaps = self._client.get_snapshots(syms, feed="iex")
        except AlpacaError as exc:
            raise NetworkError(str(exc)) from exc
        session = self._session()
        out: dict[str, Quote] = {}
        for sym, snap in snaps.items():
            quote = self._quote_from_snapshot(sym, snap, session)
            if quote is not None:
                out[sym] = quote
        return out

    def _quote(self, symbol: str) -> Quote:
        try:
            snap = self._client.get_snapshot(symbol, feed="iex")
        except AlpacaError as exc:
            raise NetworkError(str(exc)) from exc
        quote = self._quote_from_snapshot(symbol, snap, self._session())
        if quote is None:
            raise InvalidSymbolError(f"{symbol}: no market data available.")
        return quote

    def _quote_from_snapshot(self, symbol: str, snap: dict, session: str) -> Quote | None:
        if not snap:
            return None
        lt = snap.get("latestTrade") or {}
        db = snap.get("dailyBar") or {}
        pdb = snap.get("prevDailyBar") or {}
        mb = snap.get("minuteBar") or {}
        price = lt.get("p") or mb.get("c") or db.get("c")
        if price is None:
            return None
        prev_close = pdb.get("c") or db.get("o") or price
        name = self._name(symbol)
        return Quote(
            symbol=symbol.upper(),
            price=float(price),
            previous_close=float(prev_close),
            day_high=_as_float(db.get("h")),
            day_low=_as_float(db.get("l")),
            day_open=_as_float(db.get("o")),
            volume=_as_float(db.get("v")),
            currency="USD",
            exchange="",
            short_name=name,
            long_name=name,
            market_state=session,
            timestamp=datetime.now(timezone.utc),
        )

    def _bars(self, symbol: str, range_key: str) -> list[Candle]:
        timeframe, lookback, intraday = self._RANGES.get(range_key, self._RANGES["1D"])
        now = datetime.now(timezone.utc)
        end = now - timedelta(minutes=16)  # free-plan recency guard
        if range_key == "YTD":
            start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
        else:
            start = now - timedelta(days=lookback)
        try:
            raw = self._client.get_bars(
                symbol, timeframe,
                start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        except AlpacaError as exc:
            raise NetworkError(str(exc)) from exc

        candles = [
            Candle(
                time=datetime.fromisoformat(b["t"].replace("Z", "+00:00")),
                open=float(b["o"]), high=float(b["h"]), low=float(b["l"]),
                close=float(b["c"]), volume=float(b.get("v", 0.0)),
            )
            for b in raw if b.get("c") is not None
        ]
        # For 1D, keep only the most recent session's bars.
        if range_key == "1D" and candles:
            last_date = candles[-1].time.astimezone().date()
            candles = [c for c in candles if c.time.astimezone().date() == last_date]
        return candles

    def _ensure_assets(self) -> None:
        """Load the tradable-asset list once (symbol search is served from it).

        Only a *successful* load flips the flag: a transient failure here used to
        disable symbol search for the rest of the process.
        """
        if self._assets_loaded:
            return
        try:
            assets = self._client.list_assets()
        except AlpacaError:
            return
        self._assets_loaded = True
        for a in assets:
            if not a.get("tradable"):
                continue
            sym = a.get("symbol", "")
            name = a.get("name", "")
            self._assets.append((sym, name, a.get("exchange", "")))
            self._asset_names[sym] = name

    def _name(self, symbol: str) -> str:
        self._ensure_assets()
        return _clean_name(self._asset_names.get(symbol, ""))

    def _clock(self) -> dict:
        """Alpaca's market clock, cached ~30s (shared by is-open + session)."""
        import time as _time
        if self._clock_cache and _time.monotonic() - self._clock_cache[1] < 30:
            return self._clock_cache[0]
        try:
            clock = self._client.get_clock()
        except AlpacaError:
            clock = {}
        self._clock_cache = (clock, _time.monotonic())
        return clock

    def _is_open(self) -> bool:
        return bool(self._clock().get("is_open", False))

    def _session(self) -> str:
        """Current session: REGULAR / PRE / POST / CLOSED.

        Regular hours come straight from Alpaca's (holiday-aware) clock. The
        extended sessions are derived from the Eastern wall-clock windows, guarded
        by the clock's ``next_open`` date so a holiday isn't mistaken for a
        pre-market session.
        """
        clock = self._clock()
        if clock.get("is_open"):
            return "REGULAR"
        now = datetime.now(_ET)
        if now.weekday() >= 5:  # weekend
            return "CLOSED"
        minutes = now.hour * 60 + now.minute
        next_open_date = _et_date(clock.get("next_open"))
        # Pre-market: the market is due to open later *today* (reliable holiday
        # guard — on a holiday next_open would be a future date).
        if 4 * 60 <= minutes < 9 * 60 + 30 and next_open_date == now.date():
            return "PRE"
        # After-hours: today's regular session has already closed (next open is a
        # future day).
        if 16 * 60 <= minutes < 20 * 60 and next_open_date not in (None, now.date()):
            return "POST"
        return "CLOSED"


# --------------------------------------------------------------------------- #
# Provider / service factory
# --------------------------------------------------------------------------- #
def create_provider(source: str, client: AlpacaClient | None = None) -> MarketDataProvider:
    """Build a provider by name: 'alpaca', 'yahoo' (default), or 'demo'."""
    s = (source or "").strip().lower()
    if s in ("demo", "synthetic", "offline", "fake"):
        return SyntheticProvider()
    if s == "alpaca":
        if client is None:
            from ..credentials import load_alpaca
            creds = load_alpaca()
            if creds is None:
                raise MarketDataError("Alpaca credentials are not configured.")
            client = AlpacaClient(creds)
        return AlpacaProvider(client)
    return YahooProvider()


def create_service(source: str = "yahoo", client: AlpacaClient | None = None) -> MarketDataService:
    return MarketDataService(create_provider(source, client))
