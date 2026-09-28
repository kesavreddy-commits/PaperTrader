"""Data layer: the only part of the application that touches the network.

Public surface:
    MarketDataService  — cached, throttled facade used by the UI's feed thread.
    create_service     — build a service for a source ("yahoo" | "demo").
    Quote, Candle      — immutable value objects returned to the rest of the app.
    SearchResult       — symbol-search hit.
    MarketDataError, InvalidSymbolError, NetworkError — typed failures.
"""

from .models import Candle, Quote, SearchResult
from .alpaca_client import AlpacaClient, AlpacaError
from .market_data import (
    AlpacaProvider,
    InvalidSymbolError,
    MarketDataError,
    MarketDataProvider,
    MarketDataService,
    NetworkError,
    SyntheticProvider,
    YahooProvider,
    create_provider,
    create_service,
)

__all__ = [
    "Candle",
    "Quote",
    "SearchResult",
    "MarketDataService",
    "MarketDataProvider",
    "YahooProvider",
    "SyntheticProvider",
    "AlpacaProvider",
    "AlpacaClient",
    "AlpacaError",
    "create_provider",
    "create_service",
    "MarketDataError",
    "InvalidSymbolError",
    "NetworkError",
]
