"""Low-level Alpaca REST client (trading API + market-data API).

Both hosts share the same key/secret auth. This class is pure network plumbing:
it knows nothing about the trading engine or the UI. ``AlpacaProvider`` (market
data) and ``AlpacaBroker`` (account/orders) are built on top of it.

Notes learned from the API:
  * Historical bars must end at least ~15 min in the past on the free plan, so
    callers cap ``end`` accordingly; this client also transparently retries a
    subscription (403) on bars with the IEX feed.
  * The bars endpoint paginates via ``next_page_token``.
  * Snapshots use the IEX feed for a real-time (undelayed) latest price.
"""

from __future__ import annotations

import threading
import time
from collections import deque

import requests

from ..config import (
    ALPACA_MAX_CALLS_PER_MIN,
    ALPACA_RATE_WINDOW_SECONDS,
    HTTP_BACKOFF_SECONDS,
    HTTP_MAX_RETRIES,
    HTTP_TIMEOUT,
)
from ..credentials import AlpacaCredentials


class RateLimiter:
    """Thread-safe sliding-window limiter: at most ``max_calls`` per ``window``.

    :meth:`acquire` blocks the caller until a slot is free, so bursts from the
    data feed, broker poll and order actions are paced instead of rejected.
    """

    def __init__(self, max_calls: int, window: float) -> None:
        self._max = max_calls
        self._window = window
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                cutoff = now - self._window
                while self._calls and self._calls[0] <= cutoff:
                    self._calls.popleft()
                if len(self._calls) < self._max:
                    self._calls.append(now)
                    return
                wait = self._calls[0] + self._window - now
            # Sleep outside the lock; cap the chunk so we re-check promptly.
            time.sleep(min(max(wait, 0.0) + 0.005, 1.0))

    def available(self) -> int:
        with self._lock:
            cutoff = time.monotonic() - self._window
            while self._calls and self._calls[0] <= cutoff:
                self._calls.popleft()
            return self._max - len(self._calls)


# One limiter per API key (all clients using that key share the account's quota).
_LIMITERS: dict[str, RateLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def _limiter_for(key_id: str) -> RateLimiter:
    with _LIMITERS_LOCK:
        limiter = _LIMITERS.get(key_id)
        if limiter is None:
            limiter = RateLimiter(ALPACA_MAX_CALLS_PER_MIN, ALPACA_RATE_WINDOW_SECONDS)
            _LIMITERS[key_id] = limiter
        return limiter


class AlpacaError(Exception):
    """An error returned by the Alpaca API (or a transport failure)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def is_auth(self) -> bool:
        return self.status in (401, 403)


class AlpacaClient:
    def __init__(self, creds: AlpacaCredentials) -> None:
        self._creds = creds
        self._trade = creds.base_url.rstrip("/") + "/v2"
        self._data = creds.data_url.rstrip("/") + "/v2"
        self._session = requests.Session()
        self._session.headers.update({
            "APCA-API-KEY-ID": creds.key_id,
            "APCA-API-SECRET-KEY": creds.secret_key,
            "Accept": "application/json",
        })
        # The market-data feed thread, the broker poll thread and GUI-initiated
        # order actions all share this client. requests.Session is not documented
        # as thread-safe, so one request is in flight at a time.
        self._io_lock = threading.Lock()
        # Shared per-key so the feed, poller and order actions can't collectively
        # exceed Alpaca's per-account rate limit.
        self._limiter = _limiter_for(creds.key_id)

    # ------------------------------------------------------------------ #
    # HTTP core
    # ------------------------------------------------------------------ #
    def _request(self, method: str, url: str, *, params=None, json=None, retry=True) -> dict | list:
        last: AlpacaError | None = None
        attempts = HTTP_MAX_RETRIES + 1 if (retry and method == "GET") else 1
        for attempt in range(attempts):
            self._limiter.acquire()  # blocks to stay under the account rate limit
            try:
                with self._io_lock:
                    resp = self._session.request(
                        method, url, params=params, json=json, timeout=HTTP_TIMEOUT
                    )
            except requests.RequestException as exc:
                last = AlpacaError(f"Network error contacting Alpaca: {exc}")
            else:
                if resp.status_code < 300:
                    if not resp.content:
                        return {}
                    try:
                        return resp.json()
                    except ValueError:
                        return {}
                # Error: pull Alpaca's message if present.
                message = self._error_message(resp)
                if resp.status_code in (429,) or resp.status_code >= 500:
                    last = AlpacaError(message, resp.status_code)
                else:
                    raise AlpacaError(message, resp.status_code)
            if attempt < attempts - 1:
                time.sleep(HTTP_BACKOFF_SECONDS * (attempt + 1))
        raise last or AlpacaError("Unknown Alpaca error")

    @staticmethod
    def _error_message(resp) -> str:
        try:
            body = resp.json()
            if isinstance(body, dict) and body.get("message"):
                return f"{body['message']} (HTTP {resp.status_code})"
        except ValueError:
            pass
        return f"Alpaca request failed (HTTP {resp.status_code})"

    def _tget(self, path: str, params=None):
        return self._request("GET", self._trade + path, params=params)

    def _dget(self, path: str, params=None):
        return self._request("GET", self._data + path, params=params)

    # ------------------------------------------------------------------ #
    # Trading API
    # ------------------------------------------------------------------ #
    def get_account(self) -> dict:
        return self._tget("/account")

    def get_clock(self) -> dict:
        return self._tget("/clock")

    def list_positions(self) -> list:
        return self._tget("/positions")

    def list_orders(self, status: str = "open", limit: int = 100) -> list:
        return self._tget("/orders", {"status": status, "limit": limit, "nested": "false"})

    def submit_order(self, symbol: str, side: str, *, qty: float | None = None,
                     notional: float | None = None, type: str = "market",
                     limit_price: float | None = None, time_in_force: str = "day",
                     extended_hours: bool = False) -> dict:
        payload: dict = {"symbol": symbol, "side": side, "type": type,
                         "time_in_force": time_in_force}
        if qty is not None:
            payload["qty"] = str(qty)
        if notional is not None:
            payload["notional"] = str(round(notional, 2))
        if limit_price is not None:
            payload["limit_price"] = str(round(limit_price, 2))
        if extended_hours:
            payload["extended_hours"] = True
        # POST is not retried (never risk a duplicate order).
        return self._request("POST", self._trade + "/orders", json=payload, retry=False)

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", self._trade + f"/orders/{order_id}", retry=False)

    def close_position(self, symbol: str) -> dict:
        """Liquidate the entire position in ``symbol`` (handles fractions)."""
        return self._request("DELETE", self._trade + f"/positions/{symbol}", retry=False)

    def list_activities(self, activity_types: str = "FILL", page_size: int = 100) -> list:
        result = self._tget("/account/activities", {"activity_types": activity_types,
                                                    "page_size": page_size})
        return result if isinstance(result, list) else []

    def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> dict:
        return self._tget("/account/portfolio/history",
                          {"period": period, "timeframe": timeframe, "extended_hours": "false"})

    def list_assets(self, status: str = "active", asset_class: str = "us_equity") -> list:
        return self._tget("/assets", {"status": status, "asset_class": asset_class})

    # ------------------------------------------------------------------ #
    # Market-data API
    # ------------------------------------------------------------------ #
    def get_snapshot(self, symbol: str, feed: str = "iex") -> dict:
        return self._dget(f"/stocks/{symbol}/snapshot", {"feed": feed})

    def get_snapshots(self, symbols: list[str], feed: str = "iex") -> dict:
        if not symbols:
            return {}
        return self._dget("/stocks/snapshots", {"symbols": ",".join(symbols), "feed": feed})

    def get_bars(self, symbol: str, timeframe: str, start: str, end: str,
                 feed: str | None = None, limit: int = 10000) -> list[dict]:
        """Return OHLCV bars, following pagination. Falls back to IEX if the
        default (SIP) feed is not permitted for this account."""
        params = {"timeframe": timeframe, "start": start, "end": end,
                  "limit": limit, "adjustment": "all"}
        if feed:
            params["feed"] = feed
        bars: list[dict] = []
        page_token = None
        for _ in range(12):  # hard page cap
            if page_token:
                params["page_token"] = page_token
            try:
                data = self._dget(f"/stocks/{symbol}/bars", params)
            except AlpacaError as exc:
                # Free plans may lack SIP: retry once on IEX.
                if exc.status == 403 and "feed" not in params:
                    params["feed"] = "iex"
                    continue
                raise
            chunk = data.get("bars") or []
            bars.extend(chunk)
            page_token = data.get("next_page_token")
            if not page_token:
                break
        return bars
