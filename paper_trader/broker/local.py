"""The local (offline) broker: the built-in simulator behind the Broker API.

Wraps a :class:`Session` with the tested :class:`TradingEngine` and
:class:`Portfolio`. This is what powers demo/offline mode and needs no network.
"""

from __future__ import annotations

from datetime import datetime

from ..core.engine import OrderError, TradingEngine
from ..core.models import Order, Session, Side, Trade
from ..core.options import OptionContract
from ..core.portfolio import Portfolio, PortfolioSnapshot
from ..ui.format import fmt_money, fmt_price, fmt_shares
from .base import Broker, BrokerError


class LocalBroker(Broker):
    display_name = "Local simulator"
    is_remote = False
    supports_options = True

    def __init__(self, session: Session) -> None:
        self.session = session
        self.engine = TradingEngine(session)
        self._portfolio = Portfolio(session)

    def bind(self, session: Session) -> None:
        """Repoint at a different session (used when switching sessions)."""
        self.session = session
        self.engine = TradingEngine(session)
        self._portfolio = Portfolio(session)

    # -- read models ------------------------------------------------------- #
    def snapshot(self, price_map, prev_close_map) -> PortfolioSnapshot:
        return self._portfolio.snapshot(price_map, prev_close_map)

    def open_orders(self) -> list[Order]:
        return self.session.pending_orders

    def recent_trades(self) -> list[Trade]:
        return self.session.trades

    def position_quantity(self, symbol: str) -> float:
        return self.engine.position_quantity(symbol)

    def held_symbols(self) -> list[str]:
        return list(self.session.positions.keys())

    # -- trading ----------------------------------------------------------- #
    def buy_market(self, symbol, price, *, quantity=None, notional=None) -> str:
        try:
            t = self.engine.market_buy(symbol, price, quantity=quantity, notional=notional)
        except OrderError as exc:
            raise BrokerError(str(exc)) from exc
        return (f"Bought {fmt_shares(t.quantity)} {symbol} @ {fmt_price(t.price)} "
                f"· {fmt_money(t.gross)}")

    def sell_market(self, symbol, price, *, quantity=None, notional=None, sell_all=False) -> str:
        try:
            t = self.engine.market_sell(symbol, price, quantity=quantity,
                                        notional=notional, sell_all=sell_all)
        except OrderError as exc:
            raise BrokerError(str(exc)) from exc
        return (f"Sold {fmt_shares(t.quantity)} {symbol} @ {fmt_price(t.price)} "
                f"· {fmt_money(t.gross)}")

    def place_limit(self, symbol, side: Side, quantity, limit_price,
                    *, extended_hours: bool = False) -> str:
        # The simulator fills on price ticks regardless of session, so the
        # extended-hours flag is accepted for interface parity but has no effect.
        try:
            o = self.engine.place_limit(symbol, side, quantity, limit_price)
        except OrderError as exc:
            raise BrokerError(str(exc)) from exc
        return (f"Limit {side.value.lower()} placed: {fmt_shares(o.quantity)} "
                f"{symbol} @ {fmt_price(o.limit_price)}")

    def cancel_order(self, order_id: str) -> None:
        self.engine.cancel_order(order_id)

    def on_price_tick(self, price_map) -> list[str]:
        fills = self.engine.process_pending(price_map)
        return [f"Limit filled: {t.side.value} {fmt_shares(t.quantity)} {t.symbol} "
                f"@ {fmt_price(t.price)}" for t in fills]

    # -- options ----------------------------------------------------------- #
    def buy_option(self, contract: OptionContract, quantity, price, price_map) -> str:
        try:
            t = self.engine.trade_option(contract, Side.BUY, quantity, price, price_map)
        except OrderError as exc:
            raise BrokerError(str(exc)) from exc
        verb = "Bought" if self.engine.option_position_quantity(contract.occ_symbol) >= 0 \
            else "Bought to close"
        return (f"{verb} {int(t.quantity)} {contract.description} @ "
                f"{fmt_price(t.price)} · {fmt_money(t.gross)}")

    def sell_option(self, contract: OptionContract, quantity, price, price_map) -> str:
        try:
            t = self.engine.trade_option(contract, Side.SELL, quantity, price, price_map)
        except OrderError as exc:
            raise BrokerError(str(exc)) from exc
        return (f"Sold {int(t.quantity)} {contract.description} @ "
                f"{fmt_price(t.price)} · {fmt_money(t.gross)}")

    def option_position_quantity(self, occ_symbol: str) -> float:
        return self.engine.option_position_quantity(occ_symbol)

    def option_underlyings(self) -> list[str]:
        return self.engine.option_underlyings()

    def settle_options(self, price_map) -> list[str]:
        settled = self.engine.settle_expirations(price_map)
        out = []
        for t in settled:
            c = OptionContract.parse(t.symbol)
            out.append(f"{t.note}: {int(t.quantity)} {c.description} "
                       f"· {fmt_money(t.gross)}")
        return out

    # -- analytics --------------------------------------------------------- #
    def equity_curve(self) -> list[tuple[datetime, float]]:
        return [(p.time, p.value) for p in self.session.equity_curve]

    def analytics_session(self) -> Session:
        return self.session
