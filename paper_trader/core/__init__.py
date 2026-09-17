"""Trading core: pure business logic with no Qt and no network dependencies.

    models     — Session, Position, Trade, Order and enums (all JSON-serialisable).
    engine     — order validation and execution; the only place that mutates a Session.
    portfolio  — read-only valuation and P/L math over a Session + live prices.
    analytics  — returns, Sharpe ratio and trade statistics.

Because nothing here imports Qt or ``requests``, the entire engine is unit-
testable in isolation and could be reused behind a CLI or web front-end.
"""

from .models import (
    Order,
    OrderStatus,
    OrderType,
    Position,
    Session,
    Side,
    Trade,
)
from .engine import (
    InsufficientFundsError,
    InsufficientSharesError,
    InvalidOrderError,
    OrderError,
    TradingEngine,
)
from .portfolio import Portfolio, PositionView

__all__ = [
    "Session",
    "Position",
    "Trade",
    "Order",
    "Side",
    "OrderType",
    "OrderStatus",
    "TradingEngine",
    "OrderError",
    "InsufficientFundsError",
    "InsufficientSharesError",
    "InvalidOrderError",
    "Portfolio",
    "PositionView",
]
