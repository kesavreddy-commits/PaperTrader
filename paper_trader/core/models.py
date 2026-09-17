"""Core domain models: Session, Position, Trade, Order and their enums.

These are plain data structures with explicit JSON (de)serialisation. They hold
no behaviour beyond bookkeeping — all trading rules live in :mod:`engine` and all
valuation in :mod:`portfolio`, so this module can be imported anywhere without
pulling in business logic.

Money and share quantities are stored as ``float`` but always passed through the
rounding helpers in :mod:`paper_trader.util` at their point of mutation (in the
engine), so persisted values are clean.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from ..config import DEFAULT_STARTING_BALANCE
from .options import CONTRACT_MULTIPLIER, OptionContract

SCHEMA_VERSION = 2


# --------------------------------------------------------------------------- #
# Enums (stored as their string values in JSON)
# --------------------------------------------------------------------------- #
class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_dt(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- #
# Position
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Position:
    """An open holding in one symbol, tracked at average cost."""

    symbol: str
    quantity: float
    avg_cost: float  # average cost basis per share
    opened_at: datetime = field(default_factory=_now)

    @property
    def cost_basis(self) -> float:
        """Total capital currently invested in this position."""
        return self.quantity * self.avg_cost

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
            "opened_at": _iso(self.opened_at),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(
            symbol=d["symbol"],
            quantity=float(d["quantity"]),
            avg_cost=float(d["avg_cost"]),
            opened_at=_parse_dt(d["opened_at"]) if d.get("opened_at") else _now(),
        )


# --------------------------------------------------------------------------- #
# OptionPosition (an open single-leg options holding)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class OptionPosition:
    """An open option position, keyed by its OCC symbol.

    ``quantity`` is signed — positive for a long (bought) position and negative
    for a short (written) one. ``avg_price`` is the average premium *per share*
    (always a positive magnitude); dollar figures multiply by the standard
    :data:`~paper_trader.core.options.CONTRACT_MULTIPLIER` (100). The contract's
    identity (underlying, expiry, strike, right) is recovered from the OCC symbol.
    """

    symbol: str            # OCC option symbol, e.g. AAPL251219C00150000
    quantity: float        # signed number of contracts (+ long / − short)
    avg_price: float       # average premium per share (positive)
    opened_at: datetime = field(default_factory=_now)

    @property
    def contract(self) -> OptionContract:
        return OptionContract.parse(self.symbol)

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def premium_basis(self) -> float:
        """Absolute premium paid (long) or received (short) for the position."""
        return abs(self.quantity) * self.avg_price * CONTRACT_MULTIPLIER

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_price": self.avg_price,
            "opened_at": _iso(self.opened_at),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OptionPosition":
        return cls(
            symbol=d["symbol"].upper(),
            quantity=float(d["quantity"]),
            avg_price=float(d["avg_price"]),
            opened_at=_parse_dt(d["opened_at"]) if d.get("opened_at") else _now(),
        )


# --------------------------------------------------------------------------- #
# Trade (an executed transaction — the immutable audit log)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Trade:
    """A filled order. Trades are append-only and never modified."""

    id: str
    timestamp: datetime
    symbol: str
    side: Side
    quantity: float
    price: float           # execution price per share
    gross: float           # quantity * price
    order_type: OrderType = OrderType.MARKET
    fees: float = 0.0      # always 0 in this simulator; kept for realism/extension
    realized_pl: float = 0.0   # realised P/L booked by this trade (sells only)
    cash_after: float = 0.0    # account cash immediately after the fill
    note: str = ""
    asset_class: str = "equity"  # "equity" | "option"
    multiplier: float = 1.0      # shares per unit (100 for options, 1 for equity)

    @property
    def is_option(self) -> bool:
        return self.asset_class == "option"

    @property
    def cash_flow(self) -> float:
        """Signed effect on cash: negative for buys, positive for sells.

        ``gross`` already includes the contract multiplier, so this is correct
        for both equities and options.
        """
        if self.side is Side.BUY:
            return -(self.gross + self.fees)
        return self.gross - self.fees

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": _iso(self.timestamp),
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "price": self.price,
            "gross": self.gross,
            "order_type": self.order_type.value,
            "fees": self.fees,
            "realized_pl": self.realized_pl,
            "cash_after": self.cash_after,
            "note": self.note,
            "asset_class": self.asset_class,
            "multiplier": self.multiplier,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Trade":
        return cls(
            id=d.get("id") or new_id(),
            timestamp=_parse_dt(d["timestamp"]),
            symbol=d["symbol"],
            side=Side(d["side"]),
            quantity=float(d["quantity"]),
            price=float(d["price"]),
            gross=float(d["gross"]),
            order_type=OrderType(d.get("order_type", "MARKET")),
            fees=float(d.get("fees", 0.0)),
            realized_pl=float(d.get("realized_pl", 0.0)),
            cash_after=float(d.get("cash_after", 0.0)),
            note=d.get("note", ""),
            asset_class=d.get("asset_class", "equity"),
            multiplier=float(d.get("multiplier", 1.0)),
        )


# --------------------------------------------------------------------------- #
# Order (a resting/limit order awaiting a trigger)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Order:
    """A simulated limit order. Fills when the market crosses ``limit_price``."""

    id: str
    created_at: datetime
    symbol: str
    side: Side
    quantity: float
    limit_price: float
    order_type: OrderType = OrderType.LIMIT
    status: OrderStatus = OrderStatus.PENDING
    filled_at: datetime | None = None
    fill_price: float | None = None
    reject_reason: str = ""
    # Dollar-sized orders (a remote broker's "notional" orders) carry no share
    # count until they fill; keeping the dollar amount separate means the UI can
    # say "$500" instead of pretending 500 shares were requested.
    notional: float | None = None

    def triggers_at(self, market_price: float) -> bool:
        """True if the current market price satisfies this limit."""
        if self.side is Side.BUY:
            return market_price <= self.limit_price
        return market_price >= self.limit_price

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": _iso(self.created_at),
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "order_type": self.order_type.value,
            "status": self.status.value,
            "filled_at": _iso(self.filled_at) if self.filled_at else None,
            "fill_price": self.fill_price,
            "reject_reason": self.reject_reason,
            "notional": self.notional,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        return cls(
            id=d.get("id") or new_id(),
            created_at=_parse_dt(d["created_at"]),
            symbol=d["symbol"],
            side=Side(d["side"]),
            quantity=float(d["quantity"]),
            limit_price=float(d["limit_price"]),
            order_type=OrderType(d.get("order_type", "LIMIT")),
            status=OrderStatus(d.get("status", "PENDING")),
            filled_at=_parse_dt(d["filled_at"]) if d.get("filled_at") else None,
            fill_price=d.get("fill_price"),
            reject_reason=d.get("reject_reason", ""),
            notional=d.get("notional"),
        )


# --------------------------------------------------------------------------- #
# Equity curve point (for analytics)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class EquityPoint:
    time: datetime
    value: float

    def to_dict(self) -> dict:
        return {"t": _iso(self.time), "v": self.value}

    @classmethod
    def from_dict(cls, d: dict) -> "EquityPoint":
        return cls(time=_parse_dt(d["t"]), value=float(d["v"]))


# --------------------------------------------------------------------------- #
# Session (the top-level persisted unit)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Session:
    """One paper-trading account: cash, holdings, history and settings."""

    id: str = field(default_factory=new_id)
    name: str = "My Portfolio"
    starting_balance: float = DEFAULT_STARTING_BALANCE
    cash: float = DEFAULT_STARTING_BALANCE
    realized_pl: float = 0.0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    positions: dict[str, Position] = field(default_factory=dict)
    option_positions: dict[str, OptionPosition] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    pending_orders: list[Order] = field(default_factory=list)
    watchlist: list[str] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    # -- construction ------------------------------------------------------ #
    @classmethod
    def new(cls, name: str, starting_balance: float, watchlist: list[str]) -> "Session":
        return cls(
            name=name,
            starting_balance=starting_balance,
            cash=starting_balance,
            watchlist=list(dict.fromkeys(s.upper() for s in watchlist)),  # de-dup, keep order
        )

    # -- equity curve ------------------------------------------------------ #
    def record_equity(self, value: float, min_interval_seconds: float = 60.0,
                      max_points: int = 6000) -> None:
        """Append a portfolio-value snapshot, throttled to at most one per
        ``min_interval_seconds`` (except the very first)."""
        now = _now()
        if self.equity_curve:
            last = self.equity_curve[-1]
            if (now - last.time).total_seconds() < min_interval_seconds:
                # Update the latest point in place so the curve tracks the newest
                # value without unbounded growth.
                self.equity_curve[-1] = EquityPoint(now, value)
                return
        self.equity_curve.append(EquityPoint(now, value))
        if len(self.equity_curve) > max_points:
            # Keep the series bounded by dropping the oldest points.
            del self.equity_curve[: len(self.equity_curve) - max_points]

    # -- serialisation ----------------------------------------------------- #
    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "starting_balance": self.starting_balance,
            "cash": self.cash,
            "realized_pl": self.realized_pl,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "positions": {s: p.to_dict() for s, p in self.positions.items()},
            "option_positions": {
                s: p.to_dict() for s, p in self.option_positions.items()
            },
            "trades": [t.to_dict() for t in self.trades],
            "pending_orders": [o.to_dict() for o in self.pending_orders],
            "watchlist": list(self.watchlist),
            "equity_curve": [e.to_dict() for e in self.equity_curve],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            id=d.get("id") or new_id(),
            name=d.get("name", "My Portfolio"),
            starting_balance=float(d.get("starting_balance", DEFAULT_STARTING_BALANCE)),
            cash=float(d.get("cash", DEFAULT_STARTING_BALANCE)),
            realized_pl=float(d.get("realized_pl", 0.0)),
            created_at=_parse_dt(d["created_at"]) if d.get("created_at") else _now(),
            updated_at=_parse_dt(d["updated_at"]) if d.get("updated_at") else _now(),
            positions={
                s.upper(): Position.from_dict(p) for s, p in d.get("positions", {}).items()
            },
            option_positions={
                s.upper(): OptionPosition.from_dict(p)
                for s, p in d.get("option_positions", {}).items()
            },
            trades=[Trade.from_dict(t) for t in d.get("trades", [])],
            pending_orders=[Order.from_dict(o) for o in d.get("pending_orders", [])],
            watchlist=[s.upper() for s in d.get("watchlist", [])],
            equity_curve=[EquityPoint.from_dict(e) for e in d.get("equity_curve", [])],
            schema_version=int(d.get("schema_version", SCHEMA_VERSION)),
        )

    def copy(self) -> "Session":
        """A deep-ish copy (used for reset/undo semantics if needed)."""
        return Session.from_dict(self.to_dict())
