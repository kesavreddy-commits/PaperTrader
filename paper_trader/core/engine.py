"""The trading engine — order validation and execution.

This is the single place allowed to mutate a :class:`Session`. Every rule the
simulator enforces lives here:

* you cannot spend more cash than you have (buys);
* you cannot sell more shares than you own (sells);
* orders below a minimum value or that round to zero shares are rejected;
* buys update the position's average cost; sells realise P/L against it.

The same private ``_apply_buy`` / ``_apply_sell`` primitives back both market
orders and triggered limit fills, so there is exactly one implementation of the
accounting logic. All monetary and share values are rounded at mutation time via
:mod:`paper_trader.util`.
"""

from __future__ import annotations

import math

from ..config import MIN_ORDER_VALUE
from ..util import is_effectively_zero_shares, round_money, round_shares
from .models import (
    Order,
    OrderStatus,
    OrderType,
    OptionPosition,
    Position,
    Session,
    Side,
    Trade,
    _now,
    new_id,
)
from .options import (
    CONTRACT_MULTIPLIER,
    OptionContract,
    collateral_per_contract,
)

_EPSILON = 1e-9


# --------------------------------------------------------------------------- #
# Typed order errors — the UI shows their messages verbatim.
# --------------------------------------------------------------------------- #
class OrderError(Exception):
    """Base class for all order-rejection reasons."""


class InsufficientFundsError(OrderError):
    """A buy would cost more cash than is available."""


class InsufficientSharesError(OrderError):
    """A sell exceeds the number of shares owned (or uncommitted)."""


class InvalidOrderError(OrderError):
    """The order is malformed (bad price, zero quantity, below minimum…)."""


def _truncate_shares(value: float) -> float:
    """Round *down* to share precision — used when sizing a buy to a dollar
    budget so we can never exceed it by a rounding cent."""
    return math.floor(value * 1_000_000) / 1_000_000


class TradingEngine:
    """Executes orders against a :class:`Session`."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    # Convenience queries used by the UI
    # ------------------------------------------------------------------ #
    def position_quantity(self, symbol: str) -> float:
        pos = self.session.positions.get(symbol.upper())
        return pos.quantity if pos else 0.0

    def max_buy_quantity(self, price: float) -> float:
        """Largest whole+fractional share count affordable at ``price``."""
        if price <= 0:
            return 0.0
        return _truncate_shares(self.session.cash / price)

    def committed_sell_shares(self, symbol: str) -> float:
        """Shares already reserved by resting SELL limit orders."""
        symbol = symbol.upper()
        return round_shares(
            sum(
                o.quantity
                for o in self.session.pending_orders
                if o.symbol == symbol and o.side is Side.SELL and o.status is OrderStatus.PENDING
            )
        )

    # ------------------------------------------------------------------ #
    # Market orders
    # ------------------------------------------------------------------ #
    def market_buy(
        self,
        symbol: str,
        price: float,
        *,
        quantity: float | None = None,
        notional: float | None = None,
        note: str = "",
    ) -> Trade:
        """Buy at ``price`` either a share ``quantity`` or a dollar ``notional``."""
        symbol = symbol.upper()
        qty = self._resolve_buy_quantity(price, quantity, notional)
        return self._apply_buy(symbol, price, qty, OrderType.MARKET, note)

    def market_sell(
        self,
        symbol: str,
        price: float,
        *,
        quantity: float | None = None,
        notional: float | None = None,
        sell_all: bool = False,
        note: str = "",
    ) -> Trade:
        """Sell at ``price`` a share ``quantity``, a dollar ``notional``, or all."""
        symbol = symbol.upper()
        qty = self._resolve_sell_quantity(symbol, price, quantity, notional, sell_all)
        return self._apply_sell(symbol, price, qty, OrderType.MARKET, note)

    # ------------------------------------------------------------------ #
    # Limit orders
    # ------------------------------------------------------------------ #
    def place_limit(
        self, symbol: str, side: Side, quantity: float, limit_price: float
    ) -> Order:
        """Create a resting limit order (no cash is reserved until it fills)."""
        symbol = symbol.upper()
        if limit_price is None or not math.isfinite(limit_price) or limit_price <= 0:
            raise InvalidOrderError("Limit price must be a positive number.")
        qty = round_shares(quantity)
        if qty <= 0:
            raise InvalidOrderError("Order quantity must be greater than zero.")
        if qty * limit_price < MIN_ORDER_VALUE:
            raise InvalidOrderError(f"Order value must be at least ${MIN_ORDER_VALUE:.2f}.")

        if side is Side.SELL:
            owned = self.position_quantity(symbol)
            available = round_shares(owned - self.committed_sell_shares(symbol))
            if qty > available + _EPSILON:
                raise InsufficientSharesError(
                    f"Only {available:g} uncommitted share(s) of {symbol} available to sell."
                )

        order = Order(
            id=new_id(),
            created_at=_now(),
            symbol=symbol,
            side=side,
            quantity=qty,
            limit_price=round(float(limit_price), 4),
        )
        self.session.pending_orders.append(order)
        self.session.updated_at = _now()
        return order

    def cancel_order(self, order_id: str) -> Order | None:
        for o in self.session.pending_orders:
            if o.id == order_id and o.status is OrderStatus.PENDING:
                o.status = OrderStatus.CANCELLED
                self.session.updated_at = _now()
                return o
        return None

    def clear_inactive_orders(self) -> None:
        """Drop filled/cancelled/rejected orders from the working list."""
        self.session.pending_orders = [
            o for o in self.session.pending_orders if o.status is OrderStatus.PENDING
        ]

    def prune_inactive_orders(self, keep: int = 50) -> None:
        """Keep the order list bounded.

        Filled/cancelled/rejected orders are worth showing for a while (the
        Orders tab lists them), but they are never acted on again, so we retain
        only the ``keep`` most recent ones. Without this the list — and the saved
        session file — grows without limit over a long-running account.
        """
        pending = [o for o in self.session.pending_orders if o.status is OrderStatus.PENDING]
        inactive = [o for o in self.session.pending_orders if o.status is not OrderStatus.PENDING]
        if len(inactive) <= keep:
            return
        inactive.sort(key=lambda o: o.filled_at or o.created_at)
        self.session.pending_orders = pending + inactive[-keep:]

    def process_pending(self, price_map: dict[str, float]) -> list[Trade]:
        """Fill any resting limit orders whose trigger price has been reached.

        Called on every price tick with the latest known prices. Buys fill when
        the market trades at or below the limit; sells when at or above it. Fills
        execute at the current market price (price improvement is possible). A
        buy that can no longer be afforded is marked REJECTED rather than filled.
        Returns the list of fills produced this call.
        """
        fills: list[Trade] = []
        for order in self.session.pending_orders:
            if order.status is not OrderStatus.PENDING:
                continue
            market_price = price_map.get(order.symbol)
            if market_price is None or market_price <= 0:
                continue
            if not order.triggers_at(market_price):
                continue
            try:
                if order.side is Side.BUY:
                    trade = self._apply_buy(
                        order.symbol, market_price, order.quantity,
                        OrderType.LIMIT, note=f"Limit @ {order.limit_price:g}",
                    )
                else:
                    trade = self._apply_sell(
                        order.symbol, market_price, order.quantity,
                        OrderType.LIMIT, note=f"Limit @ {order.limit_price:g}",
                    )
            except OrderError as exc:
                order.status = OrderStatus.REJECTED
                order.reject_reason = str(exc)
                continue
            order.status = OrderStatus.FILLED
            order.filled_at = _now()
            order.fill_price = trade.price
            fills.append(trade)
        return fills

    # ------------------------------------------------------------------ #
    # Quantity resolution
    # ------------------------------------------------------------------ #
    @staticmethod
    def _require_price(price: float) -> None:
        if price is None or not math.isfinite(price) or price <= 0:
            raise InvalidOrderError("No valid market price is available for this symbol yet.")

    def _resolve_buy_quantity(
        self, price: float, quantity: float | None, notional: float | None
    ) -> float:
        self._require_price(price)
        if (quantity is None) == (notional is None):
            raise InvalidOrderError("Specify either a share quantity or a dollar amount.")
        if notional is not None:
            if notional <= 0 or not math.isfinite(notional):
                raise InvalidOrderError("Dollar amount must be positive.")
            qty = _truncate_shares(notional / price)  # truncate so we stay within budget
        else:
            if quantity <= 0 or not math.isfinite(quantity):
                raise InvalidOrderError("Share quantity must be positive.")
            qty = round_shares(quantity)
        if qty <= 0:
            raise InvalidOrderError("Order quantity rounds to zero shares.")
        if qty * price < MIN_ORDER_VALUE:
            raise InvalidOrderError(f"Order value must be at least ${MIN_ORDER_VALUE:.2f}.")
        return qty

    def _resolve_sell_quantity(
        self,
        symbol: str,
        price: float,
        quantity: float | None,
        notional: float | None,
        sell_all: bool,
    ) -> float:
        pos = self.session.positions.get(symbol)
        if pos is None or pos.quantity <= 0:
            raise InsufficientSharesError(f"You have no shares of {symbol} to sell.")
        # Shares already reserved by resting SELL limits cannot be sold again —
        # otherwise the limit order would be rejected later, after the fact.
        committed = self.committed_sell_shares(symbol)
        available = round_shares(pos.quantity - committed)
        if available <= 0:
            raise InsufficientSharesError(
                f"All {pos.quantity:g} share(s) of {symbol} are reserved by open "
                f"sell orders. Cancel one to free them up."
            )
        if sell_all:
            return available
        self._require_price(price)
        if (quantity is None) == (notional is None):
            raise InvalidOrderError("Specify either a share quantity or a dollar amount.")
        if notional is not None:
            if notional <= 0 or not math.isfinite(notional):
                raise InvalidOrderError("Dollar amount must be positive.")
            # Dollar targets are approximate; cap at the shares actually sellable.
            qty = min(round_shares(notional / price), available)
        else:
            if quantity <= 0 or not math.isfinite(quantity):
                raise InvalidOrderError("Share quantity must be positive.")
            qty = round_shares(quantity)
        if qty <= 0:
            raise InvalidOrderError("Order quantity rounds to zero shares.")
        if qty > available + _EPSILON:
            if committed:
                raise InsufficientSharesError(
                    f"Only {available:g} of your {pos.quantity:g} {symbol} share(s) are "
                    f"uncommitted — the rest are reserved by open sell orders."
                )
            raise InsufficientSharesError(
                f"You can sell at most {pos.quantity:g} share(s) of {symbol}."
            )
        return qty

    # ------------------------------------------------------------------ #
    # Accounting primitives (the only mutators of cash/positions)
    # ------------------------------------------------------------------ #
    def _apply_buy(
        self, symbol: str, price: float, qty: float, order_type: OrderType, note: str
    ) -> Trade:
        qty = round_shares(qty)
        cost = round_money(qty * price)
        if cost > round_money(self.session.cash) + _EPSILON:
            raise InsufficientFundsError(
                f"Insufficient funds: buying {qty:g} {symbol} costs "
                f"${cost:,.2f} but only ${self.session.cash:,.2f} is available."
            )

        pos = self.session.positions.get(symbol)
        if pos is not None:
            new_qty = round_shares(pos.quantity + qty)
            new_avg = (pos.cost_basis + qty * price) / new_qty
            pos.quantity = new_qty
            pos.avg_cost = round(new_avg, 6)
        else:
            self.session.positions[symbol] = Position(
                symbol=symbol, quantity=qty, avg_cost=round(float(price), 6)
            )

        self.session.cash = round_money(self.session.cash - cost)
        trade = Trade(
            id=new_id(),
            timestamp=_now(),
            symbol=symbol,
            side=Side.BUY,
            quantity=qty,
            price=round(float(price), 4),
            gross=cost,
            order_type=order_type,
            realized_pl=0.0,
            cash_after=self.session.cash,
            note=note,
        )
        self.session.trades.append(trade)
        self.session.updated_at = _now()
        return trade

    def _apply_sell(
        self, symbol: str, price: float, qty: float, order_type: OrderType, note: str
    ) -> Trade:
        pos = self.session.positions.get(symbol)
        if pos is None or pos.quantity <= 0:
            raise InsufficientSharesError(f"You have no shares of {symbol} to sell.")
        qty = round_shares(qty)
        if qty > round_shares(pos.quantity) + _EPSILON:
            raise InsufficientSharesError(
                f"You can sell at most {pos.quantity:g} share(s) of {symbol}."
            )
        qty = min(qty, pos.quantity)  # absorb sub-precision overshoot

        proceeds = round_money(qty * price)
        realized = round_money(qty * (price - pos.avg_cost))

        pos.quantity = round_shares(pos.quantity - qty)
        if is_effectively_zero_shares(pos.quantity):
            del self.session.positions[symbol]

        self.session.cash = round_money(self.session.cash + proceeds)
        self.session.realized_pl = round_money(self.session.realized_pl + realized)
        trade = Trade(
            id=new_id(),
            timestamp=_now(),
            symbol=symbol,
            side=Side.SELL,
            quantity=qty,
            price=round(float(price), 4),
            gross=proceeds,
            order_type=order_type,
            realized_pl=realized,
            cash_after=self.session.cash,
            note=note,
        )
        self.session.trades.append(trade)
        self.session.updated_at = _now()
        return trade

    # ================================================================== #
    # Options
    # ================================================================== #
    # Options are single-leg and settled in cash. A position's quantity is
    # *signed*: positive = long (bought), negative = short (written). Buys move
    # the position up, sells move it down; whichever part crosses back toward
    # zero realises P/L, and the remainder (if the position extends) re-averages.
    # Shorts are fully cash-secured — the reserved collateral (see
    # :func:`options.collateral_per_contract`) is held out of buying power.
    # ------------------------------------------------------------------ #
    def option_position_quantity(self, occ_symbol: str) -> float:
        pos = self.session.option_positions.get(occ_symbol.upper())
        return pos.quantity if pos else 0.0

    def option_underlyings(self) -> list[str]:
        """Distinct underlying tickers across all open option positions."""
        seen = dict.fromkeys(
            p.contract.underlying for p in self.session.option_positions.values()
        )
        return list(seen)

    def total_option_collateral(self, price_map: dict[str, float]) -> float:
        """Cash reserved to secure all open short option positions."""
        total = 0.0
        for pos in self.session.option_positions.values():
            if pos.quantity < 0:
                c = pos.contract
                up = price_map.get(c.underlying, 0.0)
                total += abs(pos.quantity) * collateral_per_contract(c, up)
        return round_money(total)

    def option_buying_power(self, price_map: dict[str, float]) -> float:
        """Cash available once short-option collateral is set aside."""
        return round_money(self.session.cash - self.total_option_collateral(price_map))

    def trade_option(
        self,
        contract: OptionContract,
        side: Side,
        quantity: float,
        price: float,
        price_map: dict[str, float],
        *,
        order_type: OrderType = OrderType.MARKET,
        note: str = "",
    ) -> Trade:
        """Buy or sell ``quantity`` contracts of ``contract`` at ``price``/share.

        ``price_map`` supplies underlying prices for collateral accounting. Raises
        an :class:`OrderError` subclass on any validation/affordability failure.
        """
        if price is None or not math.isfinite(price) or price < 0:
            raise InvalidOrderError("No valid option price is available yet.")
        qty = int(round(quantity))
        if qty <= 0:
            raise InvalidOrderError(
                "Order quantity must be a positive whole number of contracts."
            )
        if contract.is_expired():
            raise InvalidOrderError("That contract has expired.")

        occ = contract.occ_symbol
        underlying_price = price_map.get(contract.underlying, 0.0)
        mult = CONTRACT_MULTIPLIER
        pos = self.session.option_positions.get(occ)
        q = pos.quantity if pos else 0.0
        avg = pos.avg_price if pos else 0.0
        delta = qty if side is Side.BUY else -qty
        new_q = q + delta

        trade_cash = round_money(qty * price * mult)  # premium magnitude
        cash_delta = -trade_cash if side is Side.BUY else trade_cash
        resulting_cash = round_money(self.session.cash + cash_delta)

        # Affordability: never overdraw cash, never leave buying power negative.
        current_col = self.total_option_collateral(price_map)
        old_col = abs(q) * collateral_per_contract(contract, underlying_price) if q < 0 else 0.0
        new_col = (abs(new_q) * collateral_per_contract(contract, underlying_price)
                   if new_q < 0 else 0.0)
        resulting_bp = round_money(resulting_cash - (current_col - old_col + new_col))
        if resulting_cash < -_EPSILON:
            raise InsufficientFundsError(
                f"Buying {qty} contract(s) costs ${trade_cash:,.2f} but only "
                f"${self.session.cash:,.2f} cash is available."
            )
        if resulting_bp < -_EPSILON:
            raise InsufficientFundsError(
                f"Writing {qty} contract(s) requires "
                f"${(current_col - old_col + new_col):,.2f} of collateral, which "
                f"exceeds your available buying power."
            )

        # Realised P/L on the closing portion; re-average any opening remainder.
        realized = 0.0
        if q == 0:
            new_avg = price
        elif (q > 0) == (delta > 0):  # same direction — grow the position
            new_avg = (abs(q) * avg + abs(delta) * price) / (abs(q) + abs(delta))
        else:                          # opposite direction — close (maybe flip)
            closed = min(abs(delta), abs(q))
            if q > 0:                  # selling a long
                realized = closed * (price - avg) * mult
            else:                      # buying back a short
                realized = closed * (avg - price) * mult
            if abs(delta) < abs(q):
                new_avg = avg          # partial close, same side remains
            elif abs(delta) == abs(q):
                new_avg = 0.0          # flat
            else:
                new_avg = price        # flipped through zero
        realized = round_money(realized)

        # Commit.
        self.session.cash = resulting_cash
        if abs(new_q) < 1e-9:
            self.session.option_positions.pop(occ, None)
        elif pos is not None:
            pos.quantity = new_q
            pos.avg_price = round(float(new_avg), 6)
        else:
            self.session.option_positions[occ] = OptionPosition(
                symbol=occ, quantity=new_q, avg_price=round(float(new_avg), 6)
            )
        if realized:
            self.session.realized_pl = round_money(self.session.realized_pl + realized)

        trade = Trade(
            id=new_id(),
            timestamp=_now(),
            symbol=occ,
            side=side,
            quantity=float(qty),
            price=round(float(price), 4),
            gross=trade_cash,
            order_type=order_type,
            realized_pl=realized,
            cash_after=self.session.cash,
            note=note,
            asset_class="option",
            multiplier=float(mult),
        )
        self.session.trades.append(trade)
        self.session.updated_at = _now()
        return trade

    def settle_expirations(
        self, price_map: dict[str, float], now=None
    ) -> list[Trade]:
        """Cash-settle any positions whose contracts have expired.

        Long ITM contracts are auto-exercised for their intrinsic value; short
        ITM contracts are assigned (you pay intrinsic); everything OTM expires
        worthless. Settlement is deferred for an underlying with no known price.
        Returns the settlement trades produced.
        """
        now = now or _now()
        settled: list[Trade] = []
        for occ in list(self.session.option_positions.keys()):
            pos = self.session.option_positions[occ]
            contract = pos.contract
            if not contract.is_expired(now):
                continue
            up = price_map.get(contract.underlying)
            if up is None or up <= 0:
                continue  # settle once we have a price for the underlying
            intrinsic = round(contract.intrinsic_value(up), 4)
            q = pos.quantity
            mult = CONTRACT_MULTIPLIER
            gross = round_money(abs(q) * intrinsic * mult)
            if q > 0:  # long — receive intrinsic
                realized = round_money(q * (intrinsic - pos.avg_price) * mult)
                self.session.cash = round_money(self.session.cash + gross)
                side = Side.SELL
                note = "Exercised" if intrinsic > 0 else "Expired worthless"
            else:      # short — pay intrinsic on assignment
                realized = round_money(abs(q) * (pos.avg_price - intrinsic) * mult)
                self.session.cash = round_money(self.session.cash - gross)
                side = Side.BUY
                note = "Assigned" if intrinsic > 0 else "Expired worthless"
            self.session.realized_pl = round_money(self.session.realized_pl + realized)
            trade = Trade(
                id=new_id(),
                timestamp=now,
                symbol=occ,
                side=side,
                quantity=float(abs(q)),
                price=intrinsic,
                gross=gross,
                order_type=OrderType.MARKET,
                realized_pl=realized,
                cash_after=self.session.cash,
                note=note,
                asset_class="option",
                multiplier=float(mult),
            )
            self.session.trades.append(trade)
            del self.session.option_positions[occ]
            settled.append(trade)
        if settled:
            self.session.updated_at = _now()
        return settled
