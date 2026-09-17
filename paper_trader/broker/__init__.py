"""Broker layer: abstracts *where* orders execute and account state lives.

    Broker        — interface used by the UI for account/positions/orders/trading.
    LocalBroker   — the built-in simulator (Session + TradingEngine), offline.
    AlpacaBroker  — a real Alpaca paper-trading account over REST.

Both return the same DTOs the UI already knows — ``PortfolioSnapshot`` /
``PositionView`` (valuation) and ``core.models.Order`` / ``Trade`` (orders and
fills) — so the widgets render identically regardless of backend.
"""

from .base import Broker, BrokerError, empty_snapshot
from .local import LocalBroker
from .alpaca import AlpacaBroker

__all__ = ["Broker", "BrokerError", "LocalBroker", "AlpacaBroker", "empty_snapshot"]
