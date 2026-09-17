"""The Broker interface and shared helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..core.models import Order, Session, Side, Trade
from ..core.options import OptionContract
from ..core.portfolio import PortfolioSnapshot


class BrokerError(Exception):
    """An order was rejected or the broker backend failed."""


class Broker(ABC):
    """Backend for account state and order execution.

    ``is_remote`` tells the UI whether it must poll (network backends) or can
    update synchronously (the local simulator). ``supports_options`` gates the
    options UI — only the local simulator prices and trades options in this build.
    """

    display_name: str = "Broker"
    is_remote: bool = False
    supports_options: bool = False

    def refresh(self) -> None:
        """Fetch the latest account/positions/orders (no-op for local)."""

    # -- read models ------------------------------------------------------- #
    @abstractmethod
    def snapshot(self, price_map: dict[str, float],
                 prev_close_map: dict[str, float]) -> PortfolioSnapshot: ...

    @abstractmethod
    def open_orders(self) -> list[Order]: ...

    @abstractmethod
    def recent_trades(self) -> list[Trade]: ...

    @abstractmethod
    def position_quantity(self, symbol: str) -> float: ...

    def held_symbols(self) -> list[str]:
        return []

    # -- trading ----------------------------------------------------------- #
    @abstractmethod
    def buy_market(self, symbol: str, price: float | None, *,
                   quantity: float | None = None, notional: float | None = None) -> str: ...

    @abstractmethod
    def sell_market(self, symbol: str, price: float | None, *,
                    quantity: float | None = None, notional: float | None = None,
                    sell_all: bool = False) -> str: ...

    @abstractmethod
    def place_limit(self, symbol: str, side: Side, quantity: float,
                    limit_price: float, *, extended_hours: bool = False) -> str: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> None: ...

    # -- options ----------------------------------------------------------- #
    _NO_OPTIONS = ("Options trading is only available on the local simulator "
                   "in this build. Switch Trading Account → Local simulator.")

    def buy_option(self, contract: OptionContract, quantity: float, price: float,
                   price_map: dict[str, float]) -> str:
        raise BrokerError(self._NO_OPTIONS)

    def sell_option(self, contract: OptionContract, quantity: float, price: float,
                    price_map: dict[str, float]) -> str:
        raise BrokerError(self._NO_OPTIONS)

    def option_position_quantity(self, occ_symbol: str) -> float:
        return 0.0

    def option_underlyings(self) -> list[str]:
        """Underlyings held via open option positions (fed prices for valuation)."""
        return []

    def settle_options(self, price_map: dict[str, float]) -> list[str]:
        """Settle expired option positions (local only). Returns fill messages."""
        return []

    # -- price-driven simulation (local only) ------------------------------ #
    def on_price_tick(self, price_map: dict[str, float]) -> list[str]:
        """Process anything triggered by new prices (e.g. local limit fills).
        Returns human-readable fill messages. Remote brokers do this server-side."""
        return []

    # -- analytics --------------------------------------------------------- #
    def equity_curve(self) -> list[tuple[datetime, float]]:
        return []

    def analytics_session(self) -> Session | None:
        """A Session populated enough for the analytics dialog, or None."""
        return None


def empty_snapshot(starting_balance: float = 0.0, cash: float = 0.0) -> PortfolioSnapshot:
    """A zeroed snapshot shown before the first data arrives."""
    return PortfolioSnapshot(
        cash=cash, buying_power=cash, holdings_value=0.0, total_value=cash,
        invested=0.0, unrealized_pl=0.0, unrealized_pl_pct=0.0, realized_pl=0.0,
        total_pl=cash - starting_balance, total_pl_pct=0.0,
        day_change=None, day_change_pct=None,
        starting_balance=starting_balance, positions=[],
    )
