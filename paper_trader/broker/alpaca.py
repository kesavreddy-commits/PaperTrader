"""AlpacaBroker — a real Alpaca paper-trading account behind the Broker API.

Account, positions, orders and fills come from Alpaca's REST API; this class
converts them into the same DTOs the UI uses for the local simulator. Because it
is a network backend (``is_remote = True``), the UI polls :meth:`refresh` on a
background thread and calls the trading methods on explicit user actions.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..config import BROKER_ACTIVITIES_EVERY
from ..core.models import (
    EquityPoint,
    Order,
    OrderStatus,
    OrderType,
    Session,
    Side,
    Trade,
    new_id,
)
from ..core.portfolio import PortfolioSnapshot, PositionView
from ..data.alpaca_client import AlpacaClient, AlpacaError
from ..util import round_money, safe_div
from .base import Broker, BrokerError, empty_snapshot

# Alpaca order status -> our simplified status.
_STATUS = {
    "new": OrderStatus.PENDING, "accepted": OrderStatus.PENDING,
    "pending_new": OrderStatus.PENDING, "accepted_for_bidding": OrderStatus.PENDING,
    "held": OrderStatus.PENDING, "partially_filled": OrderStatus.PENDING,
    "pending_cancel": OrderStatus.PENDING, "pending_replace": OrderStatus.PENDING,
    "calculated": OrderStatus.PENDING, "suspended": OrderStatus.PENDING,
    "filled": OrderStatus.FILLED, "done_for_day": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED, "expired": OrderStatus.CANCELLED,
    "replaced": OrderStatus.CANCELLED, "stopped": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
}


class AlpacaBroker(Broker):
    display_name = "Alpaca paper"
    is_remote = True

    def __init__(self, client: AlpacaClient) -> None:
        self._client = client
        self._account: dict = {}
        self._positions: list[dict] = []
        self._orders: list[dict] = []
        self._activities: list[dict] = []
        self._equity: list[tuple[datetime, float]] = []
        self._starting_balance: float | None = None
        self._refresh_count = 0

    # ------------------------------------------------------------------ #
    # Refresh (called on the polling thread)
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        try:
            self._account = self._client.get_account()
            self._positions = self._client.list_positions()
            self._orders = self._client.list_orders(status="all", limit=50)
        except AlpacaError as exc:
            raise BrokerError(str(exc)) from exc
        # Fills change rarely and are heavier — poll them less often to spare the
        # rate budget (also fetched right after a trade via request_immediate).
        if self._refresh_count % BROKER_ACTIVITIES_EVERY == 0:
            try:
                self._activities = self._client.list_activities("FILL", 100)
            except AlpacaError:
                pass  # keep last known
        self._refresh_count += 1
        if self._starting_balance is None:
            self._load_history()

    def _load_history(self) -> None:
        for period in ("all", "1A", "1M"):
            try:
                h = self._client.get_portfolio_history(period=period, timeframe="1D")
            except AlpacaError:
                continue
            ts = h.get("timestamp") or []
            eq = h.get("equity") or []
            self._equity = [
                (datetime.fromtimestamp(t, tz=timezone.utc), float(v))
                for t, v in zip(ts, eq) if v is not None
            ]
            base = h.get("base_value")
            if base:
                self._starting_balance = float(base)
                return
        self._starting_balance = _f(self._account.get("equity")) or 100_000.0

    # ------------------------------------------------------------------ #
    # Read models
    # ------------------------------------------------------------------ #
    def snapshot(self, price_map, prev_close_map) -> PortfolioSnapshot:
        acct = self._account
        if not acct:
            return empty_snapshot()

        cash = _f(acct.get("cash"))
        buying_power = _f(acct.get("buying_power"))
        equity = _f(acct.get("equity")) or _f(acct.get("portfolio_value"))
        last_equity = _f(acct.get("last_equity")) or equity
        start = self._starting_balance if self._starting_balance is not None else equity

        rows = []  # (PositionView-without-weight fields)
        holdings = invested = unrealized = 0.0
        day_total = 0.0
        day_known = False
        for p in self._positions:
            sym = p["symbol"]
            qty = _f(p.get("qty"))
            avg = _f(p.get("avg_entry_price"))
            live = price_map.get(sym)
            priced = live is not None and live > 0
            price = float(live) if priced else (_f(p.get("current_price")) or avg)
            cost_basis = round_money(_f(p.get("cost_basis")) or qty * avg)
            market_value = round_money(qty * price)
            u_pl = round_money(market_value - cost_basis)
            u_pct = safe_div(u_pl, cost_basis) * 100.0

            prev = prev_close_map.get(sym) or _f(p.get("lastday_price"))
            if priced and prev:
                d_chg = round_money(qty * (price - prev))
                d_pct = safe_div(price - prev, prev) * 100.0
                day_total += d_chg
                day_known = True
            else:
                d_chg = _f(p.get("unrealized_intraday_pl"))
                d_pct = _f(p.get("unrealized_intraday_plpc")) * 100.0
                if p.get("unrealized_intraday_pl") is not None:
                    day_total += d_chg
                    day_known = True

            holdings += market_value
            invested += cost_basis
            unrealized += u_pl
            rows.append((sym, qty, avg, cost_basis, price, priced, market_value,
                         u_pl, u_pct, d_chg, d_pct))

        holdings = round_money(holdings)
        total_value = round_money(cash + holdings)
        views = [
            PositionView(sym, qty, avg, cost_basis, price, priced, market_value,
                         u_pl, u_pct, d_chg, d_pct,
                         safe_div(market_value, total_value) if total_value else 0.0)
            for (sym, qty, avg, cost_basis, price, priced, market_value,
                 u_pl, u_pct, d_chg, d_pct) in rows
        ]
        views.sort(key=lambda v: v.market_value, reverse=True)

        total_pl = round_money(total_value - start)
        day_change = round_money(day_total) if day_known else round_money(equity - last_equity)
        realized = round_money(total_pl - unrealized)
        return PortfolioSnapshot(
            cash=round_money(cash),
            buying_power=round_money(buying_power),
            holdings_value=holdings,
            total_value=total_value,
            invested=round_money(invested),
            unrealized_pl=round_money(unrealized),
            unrealized_pl_pct=safe_div(unrealized, invested) * 100.0,
            realized_pl=realized,
            total_pl=total_pl,
            total_pl_pct=safe_div(total_pl, start) * 100.0,
            day_change=day_change,
            day_change_pct=safe_div(day_change, total_value - day_change) * 100.0,
            starting_balance=start,
            positions=views,
        )

    def open_orders(self) -> list[Order]:
        return [self._map_order(o) for o in self._orders]

    def recent_trades(self) -> list[Trade]:
        trades = [self._map_activity(a) for a in self._activities if a.get("symbol")]
        trades.sort(key=lambda t: t.timestamp)
        return trades

    def position_quantity(self, symbol: str) -> float:
        for p in self._positions:
            if p["symbol"] == symbol.upper():
                return _f(p.get("qty"))
        return 0.0

    def held_symbols(self) -> list[str]:
        return [p["symbol"] for p in self._positions]

    # ------------------------------------------------------------------ #
    # Trading (called on explicit user actions)
    # ------------------------------------------------------------------ #
    def buy_market(self, symbol, price, *, quantity=None, notional=None) -> str:
        return self._market(symbol, "buy", quantity, notional)

    def sell_market(self, symbol, price, *, quantity=None, notional=None, sell_all=False) -> str:
        if sell_all:
            try:
                self._client.close_position(symbol.upper())
            except AlpacaError as exc:
                raise BrokerError(str(exc)) from exc
            self._safe_refresh()
            return f"Submitted order to close your {symbol} position"
        return self._market(symbol, "sell", quantity, notional)

    def _market(self, symbol, side, quantity, notional) -> str:
        try:
            order = self._client.submit_order(
                symbol.upper(), side,
                qty=quantity if notional is None else None,
                notional=notional,
                type="market", time_in_force="day",
            )
        except AlpacaError as exc:
            raise BrokerError(_friendly(exc)) from exc
        self._safe_refresh()
        verb = "Buy" if side == "buy" else "Sell"
        return f"{verb} order submitted for {symbol} ({order.get('status', 'accepted')})"

    def place_limit(self, symbol, side: Side, quantity, limit_price,
                    *, extended_hours: bool = False) -> str:
        whole = int(quantity)
        if whole < 1:
            raise BrokerError("Alpaca limit orders require at least 1 whole share.")
        # Extended-hours (pre-market / after-hours) orders must be DAY limits with
        # the extended_hours flag set; otherwise rest as a normal GTC limit.
        tif = "day" if extended_hours else "gtc"
        try:
            order = self._client.submit_order(
                symbol.upper(), "buy" if side is Side.BUY else "sell",
                qty=whole, type="limit", limit_price=limit_price,
                time_in_force=tif, extended_hours=extended_hours,
            )
        except AlpacaError as exc:
            raise BrokerError(_friendly(exc)) from exc
        self._safe_refresh()
        session = "  ·  extended hours" if extended_hours else ""
        return (f"Limit {side.value.lower()} placed: {whole} {symbol} "
                f"@ {limit_price:.2f} ({order.get('status', 'accepted')}){session}")

    def cancel_order(self, order_id: str) -> None:
        try:
            self._client.cancel_order(order_id)
        except AlpacaError as exc:
            raise BrokerError(_friendly(exc)) from exc
        self._safe_refresh()

    def _safe_refresh(self) -> None:
        try:
            self.refresh()
        except BrokerError:
            pass  # the poller will catch up

    # ------------------------------------------------------------------ #
    # Analytics
    # ------------------------------------------------------------------ #
    def equity_curve(self) -> list[tuple[datetime, float]]:
        return list(self._equity)

    def analytics_session(self) -> Session:
        start = self._starting_balance or 100_000.0
        s = Session(name="Alpaca", starting_balance=start,
                    cash=_f(self._account.get("cash")))
        s.equity_curve = [EquityPoint(t, v) for t, v in self._equity]
        s.trades = self.recent_trades()
        return s

    # ------------------------------------------------------------------ #
    # Mapping helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _map_order(o: dict) -> Order:
        side = Side.BUY if o.get("side") == "buy" else Side.SELL
        otype = OrderType.LIMIT if o.get("type") == "limit" else OrderType.MARKET
        # A dollar-sized ("notional") order has no share count until it fills —
        # keep the two apart so the UI never prints dollars as a share quantity.
        qty = _f(o.get("qty")) or _f(o.get("filled_qty"))
        notional = _f(o.get("notional")) if o.get("notional") else None
        return Order(
            id=o.get("id", new_id()),
            created_at=_parse_dt(o.get("created_at")),
            symbol=o.get("symbol", ""),
            side=side,
            quantity=qty,
            notional=notional,
            limit_price=_f(o.get("limit_price")),
            order_type=otype,
            status=_STATUS.get(o.get("status", ""), OrderStatus.PENDING),
            filled_at=_parse_dt(o["filled_at"]) if o.get("filled_at") else None,
            fill_price=_f(o.get("filled_avg_price")) if o.get("filled_avg_price") else None,
        )

    @staticmethod
    def _map_activity(a: dict) -> Trade:
        side = Side.BUY if a.get("side", "").startswith("buy") else Side.SELL
        qty = _f(a.get("qty"))
        price = _f(a.get("price"))
        return Trade(
            id=a.get("id", new_id()),
            timestamp=_parse_dt(a.get("transaction_time")),
            symbol=a.get("symbol", ""),
            side=side,
            quantity=qty,
            price=price,
            gross=round_money(qty * price),
            order_type=OrderType.MARKET,
            realized_pl=0.0,
            cash_after=0.0,
            note="Alpaca",
        )


def _f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(text) -> datetime:
    if not text:
        return datetime.now(timezone.utc)
    s = text.replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)  # trim nanoseconds to micros
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(timezone.utc)


def _friendly(exc: AlpacaError) -> str:
    msg = str(exc)
    if "insufficient" in msg.lower():
        return "Insufficient buying power for this order."
    return msg
