"""Immutable value objects returned by the data layer.

These are deliberately independent of both Qt and the trading core so the same
objects can flow from the network worker into the UI and into portfolio math.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class Quote:
    """A point-in-time snapshot of a symbol's price and session context."""

    symbol: str
    price: float
    previous_close: float
    day_high: float | None = None
    day_low: float | None = None
    day_open: float | None = None
    volume: float | None = None
    currency: str = "USD"
    exchange: str = ""
    short_name: str = ""
    long_name: str = ""
    market_state: str = ""  # e.g. "REGULAR", "PRE", "POST", "CLOSED"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def change(self) -> float:
        """Absolute price change versus the previous close."""
        return self.price - self.previous_close

    @property
    def change_pct(self) -> float:
        """Percentage change versus the previous close (0 if no baseline)."""
        if not self.previous_close:
            return 0.0
        return (self.price - self.previous_close) / self.previous_close * 100.0

    @property
    def display_name(self) -> str:
        return self.long_name or self.short_name or self.symbol


@dataclass(frozen=True, slots=True)
class Candle:
    """A single OHLCV bar."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def epoch(self) -> float:
        """Unix timestamp (seconds) — convenient for pyqtgraph x-axes."""
        return self.time.timestamp()


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A symbol-search hit from the provider."""

    symbol: str
    name: str
    exchange: str = ""
    type: str = ""  # "EQUITY", "ETF", "INDEX", ...
