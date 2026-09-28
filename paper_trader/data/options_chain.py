"""Synthesized option chains — expirations and a priced strike ladder.

A real chain would come from an options data feed (which the free Alpaca plan and
Yahoo don't reliably expose). Instead we *construct* a realistic chain from just
the underlying's live price: standard listed expirations (weeklies rolling into
monthlies) and a strike ladder centred on the money, each contract priced by the
Black-Scholes model in :mod:`paper_trader.core.options`.

This keeps options fully offline and provider-agnostic — the chain looks and
behaves the same whether the underlying price comes from Alpaca, Yahoo or the
demo feed — and it is deterministic, so the ladder doesn't jitter between frames.
Everything here is pure (no Qt, no network).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ..core.options import (
    OptionContract,
    OptionQuote,
    OptionRight,
    market_today,
    price_contract,
    strike_increment,
)


@dataclass(frozen=True, slots=True)
class ChainRow:
    """One strike's call and put, both priced, side by side."""

    strike: float
    call_contract: OptionContract
    put_contract: OptionContract
    call: OptionQuote
    put: OptionQuote


@dataclass(frozen=True, slots=True)
class OptionChain:
    """A fully-priced chain for one underlying and expiration."""

    underlying: str
    underlying_price: float
    expiry: date
    rows: list[ChainRow]
    atm_strike: float

    def contract(self, strike: float, right: OptionRight) -> OptionContract:
        return OptionContract(self.underlying, self.expiry, strike, right)


# --------------------------------------------------------------------------- #
# Expiration calendar helpers
# --------------------------------------------------------------------------- #
def _next_fridays(start: date, count: int) -> list[date]:
    """The next ``count`` Fridays strictly after ``start``."""
    days_ahead = (4 - start.weekday()) % 7 or 7  # 4 == Friday; always move forward
    first = start + timedelta(days=days_ahead)
    return [first + timedelta(weeks=i) for i in range(count)]


def _third_friday(year: int, month: int) -> date:
    """The standard monthly expiration: the third Friday of the month."""
    d = date(year, month, 1)
    days_ahead = (4 - d.weekday()) % 7
    first_friday = d + timedelta(days=days_ahead)
    return first_friday + timedelta(weeks=2)


def _monthlies(start: date, count: int) -> list[date]:
    """The third Friday of each of the next ``count`` months."""
    out: list[date] = []
    year, month = start.year, start.month
    for _ in range(count + 1):
        tf = _third_friday(year, month)
        if tf > start:
            out.append(tf)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return out[:count]


# --------------------------------------------------------------------------- #
# Chain service
# --------------------------------------------------------------------------- #
class OptionsChainService:
    """Builds expiration lists and priced chains from a live underlying price."""

    def __init__(self, num_expirations: int = 8, strikes_each_side: int = 8) -> None:
        self._num_expirations = num_expirations
        self._strikes_each_side = strikes_each_side

    def expirations(self, today: date | None = None) -> list[date]:
        """Standard listed expirations: near-term weeklies then monthlies.

        Independent of the underlying, so the expiration bar is stable as prices
        move. Weeklies cover the next few Fridays; monthlies extend further out.
        """
        today = today or market_today()
        weeklies = _next_fridays(today, 5)
        monthlies = _monthlies(today, 6)
        merged = sorted(set(weeklies) | set(monthlies))
        return merged[: self._num_expirations]

    def default_expiration(self, today: date | None = None) -> date:
        return self.expirations(today)[0]

    def strikes(self, price: float) -> list[float]:
        """A ladder of strikes centred on the at-the-money strike."""
        inc = strike_increment(price)
        atm = round(price / inc) * inc
        n = self._strikes_each_side
        raw = [round(atm + i * inc, 2) for i in range(-n, n + 1)]
        return [s for s in raw if s > 0]

    def atm_strike(self, price: float) -> float:
        inc = strike_increment(price)
        return round(price / inc) * inc

    def chain(
        self,
        underlying: str,
        price: float,
        expiry: date,
        now: datetime | None = None,
    ) -> OptionChain:
        """Price the full call/put ladder for ``underlying`` at ``expiry``."""
        now = now or datetime.now(timezone.utc)
        underlying = underlying.upper()
        rows: list[ChainRow] = []
        for strike in self.strikes(price):
            call_c = OptionContract(underlying, expiry, strike, OptionRight.CALL)
            put_c = OptionContract(underlying, expiry, strike, OptionRight.PUT)
            rows.append(
                ChainRow(
                    strike=strike,
                    call_contract=call_c,
                    put_contract=put_c,
                    call=price_contract(call_c, price, now),
                    put=price_contract(put_c, price, now),
                )
            )
        return OptionChain(
            underlying=underlying,
            underlying_price=price,
            expiry=expiry,
            rows=rows,
            atm_strike=self.atm_strike(price),
        )
