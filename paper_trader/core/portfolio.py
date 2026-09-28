"""Portfolio valuation — pure, read-only math over a Session and a price map.

The engine mutates state; this module only *reads* it. Given the current session
and a ``{symbol: price}`` map (supplied by the UI from the latest quotes), it
produces a :class:`PortfolioSnapshot` with every number the UI needs: total
value, realised/unrealised P/L, per-position views and today's change.

Keeping valuation separate from both the engine and the data layer means the
same snapshot logic works identically for live and demo prices, and can be unit
tested with a hand-written price map.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..util import round_money, safe_div
from .models import Session, Side

_ET = ZoneInfo("America/New_York")
_EPS = 1e-9
from .options import CONTRACT_MULTIPLIER, collateral_per_contract, price_contract


@dataclass(frozen=True, slots=True)
class PositionView:
    """A position enriched with live valuation, ready to render."""

    symbol: str
    quantity: float
    avg_cost: float
    cost_basis: float
    price: float
    priced: bool               # False when no live price is available yet
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float
    day_change: float | None   # today's $ change of this holding (needs prev close)
    day_change_pct: float | None
    weight: float              # fraction of total portfolio value (0..1)


@dataclass(frozen=True, slots=True)
class OptionPositionView:
    """An option position enriched with a live Black-Scholes valuation."""

    symbol: str                # OCC symbol
    description: str
    underlying: str
    right: str                 # "CALL" | "PUT"
    strike: float
    expiry: date
    dte: int                   # calendar days to expiry
    quantity: float            # signed contracts (+ long / − short)
    avg_price: float           # average premium per share
    mark: float                # current model price per share
    priced: bool               # False until an underlying quote arrives
    underlying_price: float
    market_value: float        # signed net-liquidation contribution
    cost_basis: float          # absolute premium basis (magnitude)
    unrealized_pl: float
    unrealized_pl_pct: float
    delta: float               # per-share Greeks (chain conventions)
    theta: float
    collateral: float          # cash reserved (shorts only)
    in_the_money: bool


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """A complete, self-consistent valuation of the account at one instant."""

    cash: float
    buying_power: float
    holdings_value: float
    total_value: float
    invested: float            # cost basis of open positions
    unrealized_pl: float
    unrealized_pl_pct: float
    realized_pl: float
    total_pl: float            # total_value - starting_balance
    total_pl_pct: float
    day_change: float | None
    day_change_pct: float | None
    starting_balance: float
    positions: list[PositionView]
    option_positions: list[OptionPositionView] = field(default_factory=list)
    options_value: float = 0.0       # net signed market value of options
    options_collateral: float = 0.0  # cash reserved for short options


class Portfolio:
    """Computes valuation snapshots for a :class:`Session`."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def snapshot(
        self,
        price_map: dict[str, float],
        prev_close_map: dict[str, float] | None = None,
        now: datetime | None = None,
    ) -> PortfolioSnapshot:
        prev_close_map = prev_close_map or {}
        session = self.session
        now = now or datetime.now(timezone.utc)
        flows = self._todays_equity_flows(trading_day_start(now))

        views: list[PositionView] = []
        holdings_value = 0.0
        invested = 0.0
        unrealized = 0.0
        day_change_total = 0.0
        have_day_change = False

        for symbol, pos in session.positions.items():
            live = price_map.get(symbol)
            priced = live is not None and live > 0
            # Fall back to average cost until the first quote arrives, so totals
            # stay stable (shows zero unrealised P/L rather than a bogus value).
            price = float(live) if priced else pos.avg_cost

            market_value = round_money(pos.quantity * price)
            cost_basis = round_money(pos.cost_basis)
            u_pl = round_money(market_value - cost_basis)
            u_pl_pct = safe_div(u_pl, cost_basis) * 100.0

            d_change, d_change_pct = _day_change(
                pos.quantity, price if priced else None,
                prev_close_map.get(symbol), flows.get(symbol))
            if d_change is not None:
                day_change_total += d_change
                have_day_change = True

            holdings_value += market_value
            invested += cost_basis
            unrealized += u_pl

            views.append(
                PositionView(
                    symbol=symbol,
                    quantity=pos.quantity,
                    avg_cost=pos.avg_cost,
                    cost_basis=cost_basis,
                    price=price,
                    priced=priced,
                    market_value=market_value,
                    unrealized_pl=u_pl,
                    unrealized_pl_pct=u_pl_pct,
                    day_change=d_change,
                    day_change_pct=d_change_pct,
                    weight=0.0,  # filled in below once total is known
                )
            )

        for symbol, flow in flows.items():
            if symbol in session.positions:
                continue
            d_change, _pct = _day_change(0.0, 0.0, prev_close_map.get(symbol), flow)
            if d_change is not None:
                day_change_total += d_change
                have_day_change = True

        # --- options: value each leg at its live Black-Scholes mark -------- #
        option_views: list[OptionPositionView] = []
        options_value = 0.0
        options_collateral = 0.0
        # Short options tie up collateral rather than premium, so their basis is
        # tracked separately: it belongs in the denominator of the unrealised-P/L
        # percentage (their P/L is in the numerator) but not in "invested".
        short_premium_basis = 0.0
        for occ, opos in session.option_positions.items():
            contract = opos.contract
            live = price_map.get(contract.underlying)
            priced = live is not None and live > 0
            underlying_price = float(live) if priced else contract.strike
            quote = price_contract(contract, underlying_price, now)
            mult = CONTRACT_MULTIPLIER

            signed_mv = round_money(opos.quantity * quote.mark * mult)  # + long / − short
            cost_basis = round_money(opos.premium_basis)
            u_pl = round_money((quote.mark - opos.avg_price) * opos.quantity * mult)
            u_pl_pct = safe_div(u_pl, cost_basis) * 100.0
            collateral = (round_money(abs(opos.quantity)
                          * collateral_per_contract(contract, underlying_price))
                          if opos.quantity < 0 else 0.0)

            holdings_value += signed_mv
            options_value += signed_mv
            unrealized += u_pl
            if opos.quantity > 0:            # only longs tie up premium capital
                invested += cost_basis
            else:
                short_premium_basis += cost_basis
            options_collateral += collateral

            option_views.append(
                OptionPositionView(
                    symbol=occ,
                    description=contract.description,
                    underlying=contract.underlying,
                    right=contract.right.value,
                    strike=contract.strike,
                    expiry=contract.expiry,
                    dte=max(0, (contract.expiry - now.date()).days),
                    quantity=opos.quantity,
                    avg_price=opos.avg_price,
                    mark=quote.mark,
                    priced=priced,
                    underlying_price=underlying_price,
                    market_value=signed_mv,
                    cost_basis=cost_basis,
                    unrealized_pl=u_pl,
                    unrealized_pl_pct=u_pl_pct,
                    delta=quote.delta,
                    theta=quote.theta,
                    collateral=collateral,
                    in_the_money=quote.intrinsic > 0,
                )
            )
        # Nearest expiry first, then largest exposure.
        option_views.sort(key=lambda v: (v.expiry, -abs(v.market_value)))
        options_value = round_money(options_value)
        options_collateral = round_money(options_collateral)

        holdings_value = round_money(holdings_value)
        total_value = round_money(session.cash + holdings_value)

        # Second pass: assign portfolio weights now that the total is known.
        if total_value > 0:
            views = [
                _with_weight(v, safe_div(v.market_value, total_value))
                for v in views
            ]
        # Largest holdings first — matches how the positions table reads best.
        views.sort(key=lambda v: v.market_value, reverse=True)

        total_pl = round_money(total_value - session.starting_balance)
        day_change = round_money(day_change_total) if have_day_change else None
        if day_change is not None:
            prev_total = total_value - day_change
            day_change_pct = safe_div(day_change, prev_total) * 100.0
        else:
            day_change_pct = None

        return PortfolioSnapshot(
            cash=round_money(session.cash),
            buying_power=round_money(session.cash - options_collateral),
            holdings_value=holdings_value,
            total_value=total_value,
            invested=round_money(invested),
            unrealized_pl=round_money(unrealized),
            unrealized_pl_pct=safe_div(unrealized, invested + short_premium_basis) * 100.0,
            realized_pl=round_money(session.realized_pl),
            total_pl=total_pl,
            total_pl_pct=safe_div(total_pl, session.starting_balance) * 100.0,
            day_change=day_change,
            day_change_pct=day_change_pct,
            starting_balance=session.starting_balance,
            positions=views,
            option_positions=option_views,
            options_value=options_value,
            options_collateral=options_collateral,
        )


    def _todays_equity_flows(self, day_start: datetime) -> dict[str, "_DayFlow"]:
        """Per-symbol share and cash flows from equity fills since ``day_start``."""
        flows: dict[str, _DayFlow] = {}
        # Trades are append-only, so today's are at the end of the list.
        for trade in reversed(self.session.trades):
            if trade.is_option:
                continue
            ts = trade.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < day_start:
                break
            flow = flows.setdefault(trade.symbol, _DayFlow())
            if trade.side is Side.BUY:
                flow.bought += trade.quantity
                flow.bought_cost += trade.gross + trade.fees
            else:
                flow.sold += trade.quantity
            flow.cash_in -= trade.cash_flow
        return flows


@dataclass(slots=True)
class _DayFlow:
    bought: float = 0.0       # shares bought today
    sold: float = 0.0         # shares sold today
    bought_cost: float = 0.0  # cash spent on today's buys
    cash_in: float = 0.0      # net cash put into the symbol today (buys - sells)


def trading_day_start(now: datetime) -> datetime:
    """Midnight ET of the trading day the previous close is measured from.

    Weekends roll back to Friday: over a weekend the quote's "today" is still
    Friday's session, so a Friday buy is still one of today's.
    """
    day = now.astimezone(_ET).date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return datetime.combine(day, time.min, tzinfo=_ET)


def _day_change(
    quantity: float,
    price: float | None,
    prev_close: float | None,
    flow: _DayFlow | None,
) -> tuple[float | None, float | None]:
    """Today's $ and % change of one symbol's holding.

    Shares held since before today are measured from the previous close;
    shares bought today from what was paid for them; shares sold today
    contribute their proceeds. Equivalently: value now, minus value at the
    start of the day, minus the net cash put in since.
    """
    if price is None:
        return None, None
    flow = flow or _DayFlow()
    held_from_before = quantity - flow.bought + flow.sold
    if held_from_before < _EPS:
        held_from_before = 0.0
    elif not prev_close:
        return None, None
    start_value = held_from_before * (prev_close or 0.0)
    change = round_money(quantity * price - start_value - flow.cash_in)
    base = start_value + flow.bought_cost
    return change, safe_div(change, base) * 100.0


def _with_weight(view: PositionView, weight: float) -> PositionView:
    # PositionView is frozen; rebuild with the computed weight.
    return PositionView(
        symbol=view.symbol,
        quantity=view.quantity,
        avg_cost=view.avg_cost,
        cost_basis=view.cost_basis,
        price=view.price,
        priced=view.priced,
        market_value=view.market_value,
        unrealized_pl=view.unrealized_pl,
        unrealized_pl_pct=view.unrealized_pl_pct,
        day_change=view.day_change,
        day_change_pct=view.day_change_pct,
        weight=weight,
    )
