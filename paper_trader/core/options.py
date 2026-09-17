"""Options: contract identity, Black-Scholes valuation and a deterministic IV model.

This module is the pure-math heart of the options feature. It has no Qt, no
network and no dependency beyond the standard library, so it can be imported
anywhere (engine, portfolio, data layer, tests) and unit-tested trivially.

Three concerns live here:

* :class:`OptionContract` — the identity of a single listed contract (underlying,
  expiration, strike, call/put) with the canonical OCC symbol as its key.
* :func:`black_scholes` / :func:`greeks` — European option valuation. American
  early-exercise is ignored (fine for a simulator, and equal to European for
  non-dividend calls anyway); every contract is priced off the *underlying's*
  live price, so options work with **any** data source with no options feed.
* :func:`implied_vol` — a deterministic, per-underlying volatility surface (a
  gentle smile + short-dated term premium) so a synthesized chain feels real and
  is perfectly reproducible between runs.

The contract multiplier is the standard 100 shares per contract; premiums are
quoted *per share* (like a real chain), and dollar amounts multiply by
:data:`CONTRACT_MULTIPLIER`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from enum import Enum

CONTRACT_MULTIPLIER = 100          # shares represented by one option contract
RISK_FREE_RATE = 0.043             # flat annual risk-free rate used for pricing
_SECONDS_PER_YEAR = 365.0 * 24 * 3600
_SQRT_2 = math.sqrt(2.0)


# --------------------------------------------------------------------------- #
# Right (call / put)
# --------------------------------------------------------------------------- #
class OptionRight(str, Enum):
    CALL = "CALL"
    PUT = "PUT"

    @property
    def letter(self) -> str:
        return "C" if self is OptionRight.CALL else "P"

    @property
    def short(self) -> str:
        return "Call" if self is OptionRight.CALL else "Put"


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class OptionContract:
    """One listed option contract, identified by its canonical OCC symbol.

    ``expiry`` is the (UTC) calendar date the contract expires; ``strike`` is the
    strike price in dollars. Equality/hashing follow the frozen dataclass fields,
    so contracts can be used as dict keys, but :attr:`occ_symbol` is the stable
    string key we persist and show.
    """

    underlying: str
    expiry: date
    strike: float
    right: OptionRight

    # -- identity ---------------------------------------------------------- #
    @property
    def occ_symbol(self) -> str:
        """The 21-char OCC option symbol, e.g. ``AAPL251219C00150000``."""
        strike_millis = int(round(self.strike * 1000))
        return (
            f"{self.underlying.upper()}"
            f"{self.expiry.strftime('%y%m%d')}"
            f"{self.right.letter}"
            f"{strike_millis:08d}"
        )

    @classmethod
    def parse(cls, occ: str) -> "OptionContract":
        """Parse an OCC symbol back into a contract (inverse of :attr:`occ_symbol`)."""
        occ = occ.strip().upper()
        # The last 15 chars are fixed-width: 6 date + 1 right + 8 strike.
        if len(occ) < 16:
            raise ValueError(f"Not an option symbol: {occ!r}")
        root = occ[:-15]
        body = occ[-15:]
        yy, mm, dd = int(body[0:2]), int(body[2:4]), int(body[4:6])
        right = OptionRight.CALL if body[6] == "C" else OptionRight.PUT
        strike = int(body[7:15]) / 1000.0
        return cls(
            underlying=root,
            expiry=date(2000 + yy, mm, dd),
            strike=strike,
            right=right,
        )

    @staticmethod
    def is_option_symbol(symbol: str) -> bool:
        """Cheap check: does ``symbol`` look like an OCC option symbol?"""
        s = (symbol or "").strip().upper()
        if len(s) < 16:
            return False
        body = s[-15:]
        return (
            body[:6].isdigit()
            and body[6] in ("C", "P")
            and body[7:].isdigit()
        )

    # -- display ----------------------------------------------------------- #
    @property
    def description(self) -> str:
        """Human label, e.g. ``AAPL Dec 19 '25 $150 Call``."""
        return (
            f"{self.underlying} {self.expiry.strftime('%b %d')} "
            f"'{self.expiry.strftime('%y')} {_fmt_strike(self.strike)} {self.right.short}"
        )

    @property
    def short_label(self) -> str:
        """Compact label for tight cells, e.g. ``$150 Call``."""
        return f"{_fmt_strike(self.strike)} {self.right.short}"

    # -- time -------------------------------------------------------------- #
    def expiry_datetime(self) -> datetime:
        """Expiration as a tz-aware datetime at the 4pm ET close (≈20:00 UTC)."""
        return datetime.combine(self.expiry, time(20, 0), tzinfo=timezone.utc)

    def years_to_expiry(self, now: datetime | None = None) -> float:
        """Time to expiry in years (never negative)."""
        now = now or datetime.now(timezone.utc)
        secs = (self.expiry_datetime() - now).total_seconds()
        return max(0.0, secs / _SECONDS_PER_YEAR)

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now >= self.expiry_datetime()

    def intrinsic_value(self, underlying_price: float) -> float:
        """Per-share intrinsic value at ``underlying_price`` (>= 0)."""
        if self.right is OptionRight.CALL:
            return max(0.0, underlying_price - self.strike)
        return max(0.0, self.strike - underlying_price)

    def moneyness(self, underlying_price: float) -> str:
        """"ITM" / "ATM" / "OTM" relative to ``underlying_price``."""
        intr = self.intrinsic_value(underlying_price)
        if intr > 0.005:
            return "ITM"
        if abs(underlying_price - self.strike) <= max(0.01, underlying_price * 0.004):
            return "ATM"
        return "OTM"


# --------------------------------------------------------------------------- #
# Normal distribution helpers
# --------------------------------------------------------------------------- #
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT_2))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# --------------------------------------------------------------------------- #
# Black-Scholes valuation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class OptionQuote:
    """A model-priced snapshot of one contract: mark, bid/ask and the Greeks."""

    mark: float          # theoretical (mid) price per share
    bid: float
    ask: float
    iv: float            # implied volatility used (annualised, e.g. 0.35)
    delta: float
    gamma: float
    theta: float         # per-calendar-day
    vega: float          # per 1 vol point (0.01)
    intrinsic: float
    extrinsic: float

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)


def black_scholes(
    right: OptionRight,
    spot: float,
    strike: float,
    years: float,
    vol: float,
    rate: float = RISK_FREE_RATE,
) -> float:
    """European Black-Scholes value per share (no dividends).

    Degenerate inputs collapse gracefully to discounted intrinsic value, so the
    function is safe to call at expiry (``years == 0``) or with ``vol == 0``.
    """
    spot = max(0.0, float(spot))
    strike = max(1e-9, float(strike))
    if years <= 0 or vol <= 0 or spot <= 0:
        # Intrinsic value (calls/puts), discounting the strike for puts at T>0.
        if right is OptionRight.CALL:
            return max(0.0, spot - strike * math.exp(-rate * max(0.0, years)))
        return max(0.0, strike * math.exp(-rate * max(0.0, years)) - spot)

    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    disc = math.exp(-rate * years)
    if right is OptionRight.CALL:
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def greeks(
    right: OptionRight,
    spot: float,
    strike: float,
    years: float,
    vol: float,
    rate: float = RISK_FREE_RATE,
) -> tuple[float, float, float, float]:
    """Return ``(delta, gamma, theta_per_day, vega_per_volpoint)``.

    Theta is expressed per calendar day and vega per 1 percentage-point change in
    volatility — the conventions a trader reads on a chain.
    """
    spot = max(1e-9, float(spot))
    strike = max(1e-9, float(strike))
    if years <= 0 or vol <= 0:
        # At expiry delta is a step; the rest vanish.
        itm = (spot > strike) if right is OptionRight.CALL else (spot < strike)
        delta = (1.0 if right is OptionRight.CALL else -1.0) if itm else 0.0
        return delta, 0.0, 0.0, 0.0

    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    disc = math.exp(-rate * years)
    pdf = _norm_pdf(d1)

    gamma = pdf / (spot * vol * sqrt_t)
    vega = spot * pdf * sqrt_t / 100.0  # per 1 vol point (0.01)
    if right is OptionRight.CALL:
        delta = _norm_cdf(d1)
        theta = (-(spot * pdf * vol) / (2 * sqrt_t)
                 - rate * strike * disc * _norm_cdf(d2))
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-(spot * pdf * vol) / (2 * sqrt_t)
                 + rate * strike * disc * _norm_cdf(-d2))
    return delta, gamma, theta / 365.0, vega


# --------------------------------------------------------------------------- #
# Deterministic implied-volatility surface
# --------------------------------------------------------------------------- #
def base_volatility(underlying: str) -> float:
    """A stable per-underlying at-the-money volatility in ~[0.22, 0.65].

    Derived from a hash of the ticker so the same symbol always gets the same
    vol between runs (matching the deterministic price model in the demo feed),
    while different symbols look plausibly different.
    """
    h = 0
    for ch in underlying.upper():
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return 0.22 + (h % 4300) / 10000.0  # 0.22 .. 0.65


def implied_vol(
    underlying: str,
    spot: float,
    strike: float,
    years: float,
) -> float:
    """Model IV for one contract: a base vol plus a smile and short-dated premium.

    * A convex *smile* raises IV as the strike moves away from the money.
    * A *term premium* lifts very short-dated expirations (gamma/event risk).

    Deterministic given the inputs, so a rendered chain is stable frame-to-frame.
    """
    base = base_volatility(underlying)
    spot = max(1e-9, spot)
    log_m = math.log(max(1e-9, strike) / spot)
    smile = 1.6 * log_m * log_m                 # convex in log-moneyness
    # Short-dated options carry a term premium that decays with sqrt(time).
    term = 0.05 / math.sqrt(max(years, 1.0 / 365.0)) - 0.05
    vol = base + smile + max(0.0, term) * 0.35
    return max(0.05, min(3.0, vol))


# --------------------------------------------------------------------------- #
# Full quote (mark + synthesized bid/ask + Greeks)
# --------------------------------------------------------------------------- #
def price_contract(
    contract: OptionContract,
    underlying_price: float,
    now: datetime | None = None,
    vol: float | None = None,
) -> OptionQuote:
    """Build a complete :class:`OptionQuote` for ``contract`` off the spot price.

    The bid/ask is synthesized around the model mark with a spread that widens
    for cheaper and less-liquid contracts — enough that market orders pay a
    realistic amount of slippage in the simulator.
    """
    now = now or datetime.now(timezone.utc)
    years = contract.years_to_expiry(now)
    if vol is None:
        vol = implied_vol(contract.underlying, underlying_price, contract.strike, years)

    mark = black_scholes(contract.right, underlying_price, contract.strike, years, vol)
    mark = max(0.0, round(mark, 4))
    delta, gamma, theta, vega = greeks(
        contract.right, underlying_price, contract.strike, years, vol
    )
    intrinsic = contract.intrinsic_value(underlying_price)
    extrinsic = max(0.0, mark - intrinsic)

    # Spread: a few cents floor, widening with a percentage of the premium.
    half = max(0.02, mark * 0.02) + 0.01
    bid = max(0.0, round(mark - half, 2))
    ask = round(mark + half, 2)
    return OptionQuote(
        mark=mark, bid=bid, ask=ask, iv=vol,
        delta=delta, gamma=gamma, theta=theta, vega=vega,
        intrinsic=round(intrinsic, 4), extrinsic=round(extrinsic, 4),
    )


# --------------------------------------------------------------------------- #
# Collateral (cash-secured shorts)
# --------------------------------------------------------------------------- #
def collateral_per_contract(contract: OptionContract, underlying_price: float) -> float:
    """Cash collateral reserved per short contract.

    We model shorts as fully cash/asset-secured (no leverage), which keeps the
    simulator honest without a full margin engine:

    * a short **put** reserves the cash to buy the shares at the strike;
    * a short **call** reserves the underlying's value (covered-call equivalent),
      falling back to the strike if no live underlying price is available.
    """
    if contract.right is OptionRight.PUT:
        return contract.strike * CONTRACT_MULTIPLIER
    base = underlying_price if underlying_price and underlying_price > 0 else contract.strike
    return base * CONTRACT_MULTIPLIER


# --------------------------------------------------------------------------- #
# Strike-ladder helpers
# --------------------------------------------------------------------------- #
def strike_increment(price: float) -> float:
    """A sensible strike spacing for an underlying trading near ``price``."""
    if price < 25:
        return 1.0
    if price < 100:
        return 2.5
    if price < 200:
        return 5.0
    if price < 500:
        return 10.0
    return 25.0


def _fmt_strike(strike: float) -> str:
    if abs(strike - round(strike)) < 1e-6:
        return f"${int(round(strike))}"
    return f"${strike:.2f}".rstrip("0").rstrip(".")
